"""POST /v1/submit — validate, enqueue via arq, return job_id in <500ms."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from services.api.core.auth import verify_firebase_token, verify_device_claim
from services.api.core.config import get_settings
from services.api.core.database import get_db
from services.api.core.security import check_rate_limits, check_ip_blocked
from services.api.models.audit import AuditLog
from services.api.models.job import Job
from services.api.schemas.payload import EdgePayload
from services.api.schemas.response import SubmitResponse

logger = structlog.get_logger(__name__)
router = APIRouter()
settings = get_settings()


def _estimate_completion(crop_count: int) -> int:
    return min(5 + max(0, crop_count - 9) * 3, 30)


@router.post("/submit", response_model=SubmitResponse, status_code=202)
async def submit(
    payload: EdgePayload,
    request: Request,
    claims: dict = Depends(verify_firebase_token),
    db: AsyncSession = Depends(get_db),
) -> SubmitResponse:
    import redis.asyncio as aioredis
    ip = request.client.host if request.client else "unknown"
    user_uid: str = claims.get("uid", claims.get("user_id", ""))

    r = aioredis.from_url(
        settings.redis_url, decode_responses=True,
        socket_timeout=5, socket_connect_timeout=3,
    )
    try:
        # 1. IP soft-block check (abuse detection)
        await check_ip_blocked(r, ip)

        # 2. Maintenance mode
        maintenance = await r.get(settings.maintenance_redis_key)
        if maintenance:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"status": "maintenance", "message": maintenance},
            )

        # 3. Idempotency-Key deduplication (prevents double GPU billing on retry)
        idempotency_key = request.headers.get("Idempotency-Key", "")
        if idempotency_key:
            cached = await r.get(f"idem:{idempotency_key}")
            if cached:
                logger.info("idempotency_hit", key=idempotency_key[:16])
                return SubmitResponse.model_validate_json(cached)

        # 4. Per-user daily GPU quota (protects credit spend)
        import os
        daily_limit = int(os.getenv("GPU_DAILY_LIMIT_PER_USER", "50"))
        from datetime import date
        quota_key = f"gpu:quota:{user_uid}:{date.today().isoformat()}"
        daily_count = await r.incr(quota_key)
        if daily_count == 1:
            await r.expire(quota_key, 86400)  # TTL = 1 day
        if daily_count > daily_limit:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Daily GPU quota exceeded ({daily_limit} jobs/day). Resets at midnight UTC.",
                headers={"Retry-After": "86400"},
            )

        # 5. Device claim validation
        verify_device_claim(claims, payload.device_id)

        # 6. Multi-layer rate limiting (IP + user + device + burst)
        await check_rate_limits(r, ip=ip, user_id=user_uid, device_id=payload.device_id)

    finally:
        await r.aclose()

    # 5. Create Job + AuditLog
    job_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    job = Job(
        id=job_id,
        session_id=payload.session_id,
        device_id=payload.device_id,
        user_uid=user_uid,
        status="queued",
        crop_count=len(payload.crops),
        gcs_prefix=f"{payload.device_id}/{job_id}",
    )
    db.add(job)
    db.add(AuditLog(
        correlation_id=uuid.uuid4(),
        action="job_submitted",
        actor_uid=user_uid,
        resource_type="job",
        resource_id=str(job_id),
        payload_hash=payload.payload_hash(),
        ip_address=ip,
        user_agent=request.headers.get("user-agent"),
        metadata_={
            "device_id": payload.device_id,
            "crop_count": len(payload.crops),
            "request_id": request.headers.get("X-Request-ID", ""),
            "idempotency_key": request.headers.get("Idempotency-Key", ""),
        },
    ))
    await db.commit()

    # 6. Enqueue via arq (replaces Celery send_task)
    arq_pool = request.app.state.arq_pool
    await arq_pool.enqueue_job(
        "run_pipeline",
        str(job_id),
        payload.model_dump(mode="json"),
        job.gcs_prefix or "",
        _job_id=str(job_id),  # arq deduplication key
    )

    # 7. Prometheus counter
    try:
        from services.api.core.metrics import OCR_JOBS_TOTAL
        OCR_JOBS_TOTAL.labels(status="queued").inc()
    except Exception:
        pass

    logger.info("job_submitted", job_id=str(job_id),
               device_id=payload.device_id, crop_count=len(payload.crops))

    response = SubmitResponse(
        job_id=str(job_id),
        status="queued",
        created_at=now,
        estimated_completion_seconds=_estimate_completion(len(payload.crops)),
    )

    # Store idempotency response (TTL 24h — covers all reasonable retry windows)
    if idempotency_key:
        import redis.asyncio as aioredis
        r2 = aioredis.from_url(settings.redis_url, decode_responses=True,
                               socket_timeout=3)
        try:
            await r2.set(
                f"idem:{idempotency_key}",
                response.model_dump_json(),
                ex=86400,
            )
        except Exception:
            pass
        finally:
            await r2.aclose()

    return response
