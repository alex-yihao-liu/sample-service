import json
import logging
import os
import sys
import time

from fastapi import FastAPI, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest


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

app = FastAPI(title="Self-Healing Lab Sample Service")

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
    logger.info("sample service started successfully")


@app.get("/")
async def root():
    return {"service": "sample-service", "status": "ok"}


@app.get("/health")
async def health():
    return {"status": "healthy"}


@app.get("/metrics", include_in_schema=False)
async def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
