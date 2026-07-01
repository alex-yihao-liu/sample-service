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
        log_entry = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0]:
            log_entry["exception"] = self.formatException(record.exc_info)
        return str(log_entry)


logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

if os.getenv("FAIL_STARTUP", "false").lower() == "true":
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
    "Total HTTP requests",
    ["method", "endpoint", "http_status"],
)
REQUEST_DURATION = Histogram(
    "sample_service_http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "endpoint", "http_status"],
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
async def metrics_middleware(request: Request, call_next):
    start_time = time.time()
    response: Response = await call_next(request)
    duration = time.time() - start_time
    REQUESTS.labels(
        method=request.method,
        endpoint=request.url.path,
        http_status=response.status_code,
    ).inc()
    REQUEST_DURATION.labels(
        method=request.method,
        endpoint=request.url.path,
        http_status=response.status_code,
    ).observe(duration)
    return response


@app.on_event("startup")
async def startup():
    init_db()


@app.get("/")
async def root():
    return {"message": "Hello, World!"}


@app.get("/health")
async def health():
    return {"status": "healthy"}


@app.get("/metrics")
async def metrics():
    data = generate_latest()
    return Response(content=data, media_type=CONTENT_TYPE_LATEST)


@app.get("/todos", response_model=list[Todo])
async def list_todos():
    with db_connect() as conn:
        rows = conn.execute(
            "SELECT id, title, description, completed, created_at, updated_at FROM todos ORDER BY id"
        ).fetchall()
    return rows


@app.get("/todos/{todo_id}", response_model=Todo)
async def get_todo(todo_id: int):
    with db_connect() as conn:
        row = conn.execute(
            "SELECT id, title, description, completed, created_at, updated_at FROM todos WHERE id = %s",
            (todo_id,),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Todo not found")
    return row


@app.post("/todos", response_model=Todo, status_code=status.HTTP_201_CREATED)
async def create_todo(payload: TodoCreate):
    with db_connect() as conn:
        row = conn.execute(
            """
            INSERT INTO todos (title, description, completed)
            VALUES (%s, %s, %s)
            RETURNING id, title, description, completed, created_at, updated_at
            """,
            (payload.title, payload.description, payload.completed),
        ).fetchone()
    return row


@app.patch("/todos/{todo_id}", response_model=Todo)
async def update_todo(todo_id: int, payload: TodoUpdate):
    updates = {}
    if payload.title is not None:
        updates["title"] = payload.title
    if payload.description is not None:
        updates["description"] = payload.description
    if payload.completed is not None:
        updates["completed"] = payload.completed
    if not updates:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No fields to update")
    set_clause = ", ".join(f"{k} = %s" for k in updates)
    values = list(updates.values())
    values.append(todo_id)
    with db_connect() as conn:
        row = conn.execute(
            f"UPDATE todos SET {set_clause}, updated_at = now() WHERE id = %s RETURNING id, title, description, completed, created_at, updated_at",
            values,
        ).fetchone()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Todo not found")
    return row


@app.delete("/todos/{todo_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_todo(todo_id: int):
    with db_connect() as conn:
        conn.execute("DELETE FROM todos WHERE id = %s", (todo_id,))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
