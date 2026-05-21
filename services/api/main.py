"""
Medibox Cloud API — Cloud Run FastAPI entry point.
"""

from __future__ import annotations

import time
import uuid
from contextlib import asynccontextmanager

import structlog
import structlog.stdlib
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from services.api.core.config import get_settings
from services.api.core.telemetry import configure_metrics, configure_telemetry
from services.api.routers import admin, camera, corrections, results, submit
from services.api.ws.manager import ws_manager

settings = get_settings()

# Configure structured JSON logging for Cloud Logging
structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
)

logger = structlog.get_logger("medibox.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_telemetry("medibox-api")
    configure_metrics("medibox-api")
    await ws_manager.startup()
    logger.info("api_startup", environment=settings.environment, project=settings.gcp_project_id)
    yield
    await ws_manager.shutdown()
    logger.info("api_shutdown")


app = FastAPI(
    title="Medibox Cloud API",
    description=(
        "Distributed medical prescription OCR platform — Tunisia. "
        "**Clinical decision-support tool only. Pharmacist verification required.**"
    ),
    version="1.0.0",
    docs_url="/v1/docs",
    openapi_url="/v1/openapi.json",
    redoc_url="/v1/redoc",
    lifespan=lifespan,
)

# CORS — frontend on same domain in prod, localhost:5173 for dev
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        f"https://{settings.domain}",
        "https://medibox-frontend-5w7o5tyr2q-uc.a.run.app",
        "http://localhost:5173",
        "http://localhost:3000",
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID", "X-Correlation-ID"],
    expose_headers=["X-Request-ID", "X-Response-Time-Ms"],
)

# HSTS + security headers (Cloud Run provides TLS; we add the policy headers)
@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains; preload"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


@app.middleware("http")
async def request_context_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    t0 = time.perf_counter()
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id=request_id, path=request.url.path)
    response = await call_next(request)
    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Response-Time-Ms"] = str(elapsed_ms)
    return response


# Routes
app.include_router(submit.router, prefix="/v1", tags=["Inference"])
app.include_router(results.router, prefix="/v1", tags=["Results"])
app.include_router(admin.router, prefix="/v1", tags=["Admin"])
app.include_router(corrections.router, prefix="/v1", tags=["Corrections"])
app.include_router(camera.router, prefix="/v1", tags=["Camera"])


@app.get("/v1/healthz", tags=["Health"], include_in_schema=False)
async def healthz():
    return {"status": "ok"}


@app.get("/v1/readyz", tags=["Health"], include_in_schema=False)
async def readyz():
    import redis.asyncio as aioredis
    from sqlalchemy import text
    from services.api.core.database import engine

    errors: list[str] = []

    # Check Redis
    try:
        r = aioredis.from_url(settings.redis_url, socket_connect_timeout=2, decode_responses=True)
        maintenance = await r.get(settings.maintenance_redis_key)
        await r.aclose()
        if maintenance:
            return JSONResponse(
                status_code=503,
                content={"status": "maintenance", "message": maintenance,
                         "disclaimer": "Pharmacist verification required. Medibox assists, it does not dispense."},
            )
    except Exception as exc:
        errors.append(f"redis: {exc}")

    # Check Postgres
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:
        errors.append(f"postgres: {exc}")

    if errors:
        return JSONResponse(status_code=503, content={"status": "degraded", "errors": errors})

    return {
        "status": "ready",
        "active_ws": ws_manager.active_count,
        "disclaimer": "Pharmacist verification required. Medibox assists, it does not dispense.",
    }


@app.exception_handler(404)
async def not_found(request: Request, exc):
    return JSONResponse(status_code=404, content={"detail": "Not found"})


@app.exception_handler(500)
async def server_error(request: Request, exc):
    logger.error("unhandled_exception", path=request.url.path, exc_info=exc)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})
