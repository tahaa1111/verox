"""
Medibox Cloud API — FastAPI entry point.
Security: JWT auth, multi-layer rate limiting, abuse detection, full audit trail.
Observability: Prometheus metrics, structured JSON logs, request tracing.
"""

from __future__ import annotations

import time
import uuid
from contextlib import asynccontextmanager

import structlog
import structlog.stdlib
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse

from services.api.core.config import get_settings
from services.api.core.startup import validate_secrets
from services.api.core.telemetry import configure_telemetry
from services.api.routers import admin, camera, corrections, results, submit
from services.api.ws.manager import ws_manager

settings = get_settings()

# ---------------------------------------------------------------------------
# Structured JSON logging
# ---------------------------------------------------------------------------
structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.dict_tracebacks,
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
)

logger = structlog.get_logger("medibox.api")


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

async def _bootstrap_admin_grant() -> None:
    import os
    uid = os.getenv("ADMIN_BOOTSTRAP_UID", "").strip()
    if not uid:
        return
    try:
        from sqlalchemy import text
        from services.api.core.database import AsyncSessionLocal
        async with AsyncSessionLocal() as session:
            await session.execute(
                text("""
                    INSERT INTO admin_role_grants (user_id, granted_by, notes)
                    VALUES (:uid, 'system_bootstrap', 'Auto-provisioned admin on startup')
                    ON CONFLICT DO NOTHING
                """),
                {"uid": uid},
            )
            await session.commit()
        logger.info("admin_bootstrap_ok", uid=uid)
    except Exception as exc:
        logger.warning("admin_bootstrap_failed", uid=uid, exc=str(exc))


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Validate all critical secrets — exits immediately if any are missing/invalid
    validate_secrets()

    # 2. Optional telemetry (no-op if packages missing)
    configure_telemetry("medibox-api")

    # 3. WebSocket manager startup (Redis pub/sub)
    await ws_manager.startup()

    # 4. Bootstrap admin grant (idempotent)
    await _bootstrap_admin_grant()

    logger.info("api_startup", environment=settings.environment)
    yield
    await ws_manager.shutdown()
    logger.info("api_shutdown")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Medibox Cloud API",
    description=(
        "Distributed medical prescription OCR — Tunisia. "
        "**Clinical decision-support tool only. Pharmacist verification required.**"
    ),
    version="2.0.0",
    docs_url="/v1/docs" if settings.environment != "production" else None,
    openapi_url="/v1/openapi.json" if settings.environment != "production" else None,
    redoc_url=None,
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# CORS — strictly allowlisted, no wildcards ever
# ---------------------------------------------------------------------------

_PROD_ORIGINS = [
    "https://verox-five.vercel.app",
    f"https://{settings.domain}",
]
_DEV_ORIGINS = [
    "http://localhost:5173",
    "http://localhost:3000",
]
_cors_origins = (
    _PROD_ORIGINS + _DEV_ORIGINS
    if settings.environment != "production"
    else _PROD_ORIGINS
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID", "X-Correlation-ID"],
    expose_headers=["X-Request-ID", "X-Response-Time-Ms", "Retry-After"],
)

# ---------------------------------------------------------------------------
# Security headers middleware
# ---------------------------------------------------------------------------

@app.middleware("http")
async def security_headers_middleware(request: Request, call_next) -> Response:
    response = await call_next(request)
    response.headers["Strict-Transport-Security"] = (
        "max-age=31536000; includeSubDomains; preload"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; frame-ancestors 'none'"
    )
    response.headers["Permissions-Policy"] = (
        "geolocation=(), camera=(), microphone=(), payment=()"
    )
    response.headers["X-XSS-Protection"] = "1; mode=block"
    return response


# ---------------------------------------------------------------------------
# Request tracing + Prometheus instrumentation middleware
# ---------------------------------------------------------------------------

@app.middleware("http")
async def request_context_middleware(request: Request, call_next) -> Response:
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    trace_id = request.headers.get("X-Trace-ID", str(uuid.uuid4()))
    t0 = time.perf_counter()

    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(
        request_id=request_id,
        trace_id=trace_id,
        path=request.url.path,
        method=request.method,
        ip=request.client.host if request.client else "unknown",
    )

    response = await call_next(request)
    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    elapsed_s  = elapsed_ms / 1000.0

    # Normalize endpoint for cardinality control (avoid UUID explosion in labels)
    endpoint = _normalize_endpoint(request.url.path)

    # Prometheus counters + histograms
    try:
        from services.api.core.metrics import REQUEST_COUNT, REQUEST_LATENCY
        REQUEST_COUNT.labels(
            method=request.method,
            endpoint=endpoint,
            status_code=str(response.status_code),
        ).inc()
        REQUEST_LATENCY.labels(endpoint=endpoint).observe(elapsed_s)
    except Exception:
        pass

    response.headers["X-Request-ID"] = request_id
    response.headers["X-Trace-ID"]   = trace_id
    response.headers["X-Response-Time-Ms"] = str(elapsed_ms)

    logger.info(
        "request_completed",
        endpoint=endpoint,
        status_code=response.status_code,
        elapsed_ms=elapsed_ms,
    )

    return response


def _normalize_endpoint(path: str) -> str:
    """Replace UUID/numeric path segments with placeholders to control Prometheus cardinality."""
    import re
    path = re.sub(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        "{id}", path
    )
    path = re.sub(r"/\d+", "/{id}", path)
    return path


# ---------------------------------------------------------------------------
# /metrics — Prometheus scrape endpoint (protected by METRICS_SECRET)
# ---------------------------------------------------------------------------

@app.get("/metrics", include_in_schema=False)
async def metrics(request: Request):
    """
    Prometheus metrics endpoint.
    Requires: Authorization: Bearer <METRICS_SECRET>
    If METRICS_SECRET is not set, endpoint is disabled (returns 404).
    """
    secret = settings.metrics_secret
    if not secret:
        return JSONResponse(status_code=404, content={"detail": "Metrics not enabled"})

    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer ") or auth[7:] != secret:
        return JSONResponse(status_code=401, content={"detail": "Unauthorized"})

    from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
    return PlainTextResponse(
        content=generate_latest().decode("utf-8"),
        media_type=CONTENT_TYPE_LATEST,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

app.include_router(submit.router,      prefix="/v1", tags=["Inference"])
app.include_router(results.router,     prefix="/v1", tags=["Results"])
app.include_router(admin.router,       prefix="/v1", tags=["Admin"])
app.include_router(corrections.router, prefix="/v1", tags=["Corrections"])
app.include_router(camera.router,      prefix="/v1", tags=["Camera"])


# ---------------------------------------------------------------------------
# Health endpoints
# ---------------------------------------------------------------------------

@app.get("/v1/healthz", tags=["Health"], include_in_schema=False)
async def healthz():
    return {"status": "ok"}


@app.get("/v1/readyz", tags=["Health"], include_in_schema=False)
async def readyz():
    import redis.asyncio as aioredis
    from sqlalchemy import text
    from services.api.core.database import engine

    errors: list[str] = []

    try:
        r = aioredis.from_url(settings.redis_url, socket_connect_timeout=2, decode_responses=True)
        maintenance = await r.get(settings.maintenance_redis_key)
        await r.aclose()
        if maintenance:
            return JSONResponse(
                status_code=503,
                content={
                    "status": "maintenance",
                    "message": maintenance,
                    "disclaimer": "Pharmacist verification required.",
                },
            )
    except Exception as exc:
        errors.append(f"redis: {exc}")

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


# ---------------------------------------------------------------------------
# Error handlers
# ---------------------------------------------------------------------------

@app.exception_handler(404)
async def not_found(request: Request, exc):
    return JSONResponse(status_code=404, content={"detail": "Not found"})


@app.exception_handler(500)
async def server_error(request: Request, exc):
    logger.error("unhandled_exception", path=request.url.path, exc_info=exc)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})
