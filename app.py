import json
import logging
import os
import sys
import time
from datetime import datetime
from typing import Any

import psycopg
from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from psycopg.rows import dict_row
from pydantic import BaseModel, Field


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(
            {
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "level": record.levelname,
                "service": "sample-service",
                "message": record.getMessage(),
            }
        )


handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(JsonFormatter())
logger = logging.getLogger("sample-service")
logger.handlers = [handler]
logger.setLevel(logging.INFO)
logger.propagate = False

if os.getenv("FAIL_STARTUP", "false").lower() in {"1", "true", "yes"}:
    logger.error("startup failed because FAIL_STARTUP=true")
    raise SystemExit(1)

app = FastAPI(
    title="Self-Healing Lab Sample Service",
    description="Sample service for the self-healing lab, including Todo CRUD APIs.",
    version="1.1.0",
)

cors_origins = [
    origin.strip()
    for origin in os.getenv(
        "CORS_ALLOW_ORIGINS",
        "http://localhost:3000,http://localhost:8080,http://127.0.0.1:3000,http://127.0.0.1:8080",
    ).split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


class TodoCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = ""
    completed: bool = False


class TodoUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    completed: bool | None = None


class Todo(BaseModel):
    id: int
    title: str
    description: str
    completed: bool
    created_at: datetime
    updated_at: datetime

REQUESTS = Counter(
    "sample_service_http_requests_total",
    "HTTP requests processed by the sample service",
    ["method", "path", "status"],
)
LATENCY = Histogram(
    "sample_service_http_request_duration_seconds",
    "HTTP request latency for the sample service",
    ["method", "path"],
)


def db_settings() -> dict[str, Any]:
    return {
        "host": os.getenv("POSTGRES_HOST", "postgres.backstage.svc.cluster.local"),
        "port": int(os.getenv("POSTGRES_PORT", "5432")),
        "dbname": os.getenv("POSTGRES_DB", "demo"),
        "user": os.getenv("POSTGRES_USER", "backstage"),
        "password": os.getenv("POSTGRES_PASSWORD", "backstage-local-password"),
    }


def db_connect():
    return psycopg.connect(**db_settings(), autocommit=True, row_factory=dict_row)


def init_db() -> None:
    with db_connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS todos (
                id SERIAL PRIMARY KEY,
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                completed BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )


@app.middleware("http")
async def observe_request(request: Request, call_next):
    started = time.perf_counter()
    response = await call_next(request)
    elapsed = time.perf_counter() - started
    path = request.url.path
    REQUESTS.labels(request.method, path, str(response.status_code)).inc()
    LATENCY.labels(request.method, path).observe(elapsed)
    logger.info(
        f"request method={request.method} path={path} "
        f"status={response.status_code} duration_seconds={elapsed:.6f}"
    )
    return response


@app.on_event("startup")
async def startup_event() -> None:
    init_db()
    logger.info("sample service started successfully")


@app.get("/")
async def root():
    return {"service": "sample-service", "status": "ok"}


@app.get("/health")
async def health():
    return {"status": "healthy"}


@app.get("/todos", response_model=list[Todo], tags=["todos"])
async def list_todos():
    with db_connect() as conn:
        rows = conn.execute(
            """
            SELECT id, title, description, completed, created_at, updated_at
            FROM todos
            ORDER BY id
            """
        ).fetchall()
    return rows


@app.post(
    "/todos",
    response_model=Todo,
    status_code=status.HTTP_201_CREATED,
    tags=["todos"],
)
async def create_todo(payload: TodoCreate):
    with db_connect() as conn:
        row = conn.execute(
            """
            INSERT INTO todos (title, description, completed)
            VALUES (%s, %s, %s)
            RETURNING id, title, description, completed, created_at, updated_at
            """,
            (payload.description, payload.description, payload.completed),
        ).fetchone()
    return row


@app.get("/todos/{todo_id}", response_model=Todo, tags=["todos"])
async def get_todo(todo_id: int):
    with db_connect() as conn:
        row = conn.execute(
            """
            SELECT id, title, description, completed, created_at, updated_at
            FROM todos
            WHERE id = %s
            """,
            (todo_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Todo not found")
    return row


@app.patch("/todos/{todo_id}", response_model=Todo, tags=["todos"])
async def update_todo(todo_id: int, payload: TodoUpdate):
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        return await get_todo(todo_id)

    allowed_fields = ["title", "description", "completed"]
    set_clauses = [f"{field} = %s" for field in allowed_fields if field in updates]
    values = [updates[field] for field in allowed_fields if field in updates]
    values.append(todo_id)

    with db_connect() as conn:
        row = conn.execute(
            f"""
            UPDATE todos
            SET {", ".join(set_clauses)}, updated_at = now()
            WHERE id = %s
            RETURNING id, title, description, completed, created_at, updated_at
            """,
            values,
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Todo not found")
    return row


@app.delete("/todos/{todo_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["todos"])
async def delete_todo(todo_id: int):
    with db_connect() as conn:
        row = conn.execute(
            "DELETE FROM todos WHERE id = %s RETURNING id",
            (todo_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Todo not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get("/metrics", include_in_schema=False)
async def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
