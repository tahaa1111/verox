"""
Medibox Cloud API — FastAPI entry point.

Single-process design: arq worker pool runs inside this process alongside
the HTTP server (no separate worker service required).

Security: JWT auth, multi-layer rate limiting, abuse detection, audit trail,
PII log scrubbing, CORS, security headers, body size limit.

Observability: Prometheus metrics, structured JSON logs, request tracing, Sentry.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from contextlib import asynccontextmanager

import structlog
import structlog.stdlib
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse

from services.api.core.config import get_settings
from services.api.core.logging_processors import scrub_pii_processor
from services.api.core.startup import validate_secrets
from services.api.core.telemetry import configure_telemetry
from services.api.routers import admin, camera, corrections, results, submit, warmup
from services.api.ws.manager import ws_manager

settings = get_settings()

# ---------------------------------------------------------------------------
# Structured JSON logging with PII scrubbing as the FIRST processor
# ---------------------------------------------------------------------------
structlog.configure(
    processors=[
        scrub_pii_processor,                         # must be first
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
# arq worker — runs in the same event loop as FastAPI
# ---------------------------------------------------------------------------

async def _run_arq_worker() -> None:
    """Start arq worker pool inside the FastAPI process."""
    from arq import Worker
    from services.api.worker.arq_settings import WorkerSettings
    worker = Worker(
        functions=WorkerSettings.functions,
        redis_settings=WorkerSettings.redis_settings,
        max_jobs=WorkerSettings.max_jobs,
        job_timeout=WorkerSettings.job_timeout,
        health_check_interval=WorkerSettings.health_check_interval,
        keep_result=WorkerSettings.keep_result,
        on_startup=WorkerSettings.on_startup,
        on_shutdown=WorkerSettings.on_shutdown,
    )
    await worker.async_run()


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
                    VALUES (:uid, 'system_bootstrap', 'Auto-provisioned on startup')
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
    # 1. Fail-fast secrets validation — exits before serving traffic if invalid
    validate_secrets()

    # 2. Sentry (if DSN configured)
    if settings.sentry_dsn:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
        sentry_sdk.init(
            dsn=settings.sentry_dsn,
            environment=settings.environment,
            traces_sample_rate=0.1,
            integrations=[FastApiIntegration(), SqlalchemyIntegration()],
            before_send=_sentry_scrub_pii,
        )

    # 3. Optional OTel tracing
    configure_telemetry("medibox-api")

    # 4. WebSocket manager (Redis pub/sub)
    await ws_manager.startup()

    # 5. Bootstrap admin grant (idempotent)
    await _bootstrap_admin_grant()

    # 6. Start arq worker pool as background task (same event loop)
    arq_task = asyncio.create_task(_run_arq_worker())

    # 7. Create arq pool for job enqueueing (used by submit router)
    from arq import create_pool
    from services.api.worker.arq_settings import WorkerSettings
    app.state.arq_pool = await create_pool(WorkerSettings.redis_settings)

    logger.info("api_startup", environment=settings.environment)
    yield

    # Shutdown
    arq_task.cancel()
    await asyncio.gather(arq_task, return_exceptions=True)
    await app.state.arq_pool.aclose()
    await ws_manager.shutdown()
    logger.info("api_shutdown")


def _sentry_scrub_pii(event: dict, hint: dict) -> dict:
    """Strip PII fields from Sentry payloads before they leave the process."""
    from services.api.core.logging_processors import _PII_FIELD_NAMES
    for frame in (event.get("exception", {})
                       .get("values", [{}])[0]
                       .get("stacktrace", {})
                       .get("frames", [])):
        for var_dict in frame.get("vars", {}).values() if isinstance(
            frame.get("vars"), dict) else []:
            pass
    for key in list(event.get("extra", {}).keys()):
        if key.lower() in _PII_FIELD_NAMES:
            event["extra"][key] = "[REDACTED]"
    return event


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Medibox Cloud API",
    description=(
        "Medical prescription OCR for Tunisia. "
        "**Clinical decision-support only. Pharmacist verification required.**"
    ),
    version="3.0.0",
    # Docs disabled in production — never expose schema to unauthenticated users
    docs_url="/v1/docs" if settings.environment != "production" else None,
    openapi_url="/v1/openapi.json" if settings.environment != "production" else None,
    redoc_url=None,
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# CORS — strict allowlist, no wildcards, ever
# ---------------------------------------------------------------------------
_PROD_ORIGINS = [
    "https://verox-five.vercel.app",
    f"https://{settings.domain}",
]
_DEV_ORIGINS = ["http://localhost:5173", "http://localhost:3000"]
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
    allow_headers=["Authorization", "Content-Type",
                   "X-Request-ID", "X-Correlation-ID", "Idempotency-Key"],
    expose_headers=["X-Request-ID", "X-Trace-ID", "X-Response-Time-Ms", "Retry-After"],
)

# ---------------------------------------------------------------------------
# Body size limit — 10 MB (prevents OOM from malicious oversized uploads)
# ---------------------------------------------------------------------------
_MAX_BODY_BYTES = 10 * 1024 * 1024


@app.middleware("http")
async def body_size_limit_middleware(request: Request, call_next) -> Response:
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > _MAX_BODY_BYTES:
                return JSONResponse(
                    status_code=413,
                    content={"detail": "Request body too large. Maximum: 10MB"},
                )
        except ValueError:
            return JSONResponse(
                status_code=400,
                content={"detail": "Invalid Content-Length header"},
            )
    return await call_next(request)


# ---------------------------------------------------------------------------
# Request timeout — 30 s hard limit (prevents pile-up on slow dependencies)
# ---------------------------------------------------------------------------
@app.middleware("http")
async def request_timeout_middleware(request: Request, call_next) -> Response:
    # Skip long-polling WebSocket upgrades
    if request.headers.get("upgrade", "").lower() == "websocket":
        return await call_next(request)
    try:
        import anyio
        with anyio.fail_after(30):
            return await call_next(request)
    except TimeoutError:
        return JSONResponse(
            status_code=504,
            content={"detail": "Request timed out"},
        )


# ---------------------------------------------------------------------------
# Security headers
# ---------------------------------------------------------------------------
@app.middleware("http")
async def security_headers_middleware(request: Request, call_next) -> Response:
    response = await call_next(request)
    response.headers["Strict-Transport-Security"] = (
        "max-age=31536000; includeSubDomains; preload"
    )
    response.headers["X-Content-Type-Options"]  = "nosniff"
    response.headers["X-Frame-Options"]         = "DENY"
    response.headers["X-XSS-Protection"]        = "1; mode=block"
    response.headers["Referrer-Policy"]          = "strict-origin-when-cross-origin"
    response.headers["Content-Security-Policy"]  = (
        "default-src 'none'; frame-ancestors 'none'"
    )
    response.headers["Permissions-Policy"] = (
        "geolocation=(), camera=(), microphone=(), payment=()"
    )
    return response


# ---------------------------------------------------------------------------
# Request tracing + Prometheus instrumentation
# ---------------------------------------------------------------------------
@app.middleware("http")
async def request_context_middleware(request: Request, call_next) -> Response:
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    trace_id   = request.headers.get("X-Trace-ID",   str(uuid.uuid4()))
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
    endpoint = _normalize_endpoint(request.url.path)

    try:
        from services.api.core.metrics import REQUEST_COUNT, REQUEST_LATENCY
        REQUEST_COUNT.labels(
            method=request.method,
            endpoint=endpoint,
            status_code=str(response.status_code),
        ).inc()
        REQUEST_LATENCY.labels(endpoint=endpoint).observe(elapsed_ms / 1000)
    except Exception:
        pass

    response.headers["X-Request-ID"]       = request_id
    response.headers["X-Trace-ID"]         = trace_id
    response.headers["X-Response-Time-Ms"] = str(elapsed_ms)
    logger.info("request_completed", endpoint=endpoint,
               status_code=response.status_code, elapsed_ms=elapsed_ms)
    return response


def _normalize_endpoint(path: str) -> str:
    import re
    path = re.sub(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        "{id}", path,
    )
    return re.sub(r"/\d+", "/{id}", path)


# ---------------------------------------------------------------------------
# /metrics — Prometheus scrape endpoint (protected)
# ---------------------------------------------------------------------------
@app.get("/metrics", include_in_schema=False)
async def metrics_endpoint(request: Request):
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
app.include_router(warmup.router,      prefix="/v1", tags=["Warmup"])


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
@app.get("/v1/healthz", include_in_schema=False)
async def healthz():
    return {"status": "ok"}


@app.get("/v1/readyz", include_in_schema=False)
async def readyz():
    import redis.asyncio as aioredis
    from sqlalchemy import text
    from services.api.core.database import engine

    errors: list[str] = []
    try:
        r = aioredis.from_url(
            settings.redis_url, socket_connect_timeout=2, decode_responses=True
        )
        maintenance = await r.get(settings.maintenance_redis_key)
        await r.aclose()
        if maintenance:
            return JSONResponse(
                status_code=503,
                content={"status": "maintenance", "message": maintenance},
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
        "disclaimer": "Pharmacist verification required.",
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
