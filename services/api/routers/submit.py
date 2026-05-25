"""POST /v1/submit — validate, enqueue, return job_id in < 500ms."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from services.api.core.auth import verify_firebase_token, verify_device_claim
from services.api.core.config import get_settings
from services.api.core.database import get_db
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
    # Check maintenance mode + rate limit using a single Redis connection
    import redis.asyncio as aioredis
    r = aioredis.from_url(settings.redis_url, decode_responses=True)
    try:
        maintenance = await r.get(settings.maintenance_redis_key)
        if maintenance:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"status": "maintenance", "message": maintenance},
            )
        # Verify device_id claim matches payload
        verify_device_claim(claims, payload.device_id)
        # Per-device rate limiting (Redis token bucket)
        await _check_rate_limit(r, payload.device_id)
    finally:
        await r.aclose()

    job_id = uuid.uuid4()
    user_uid: str = claims.get("uid", claims.get("user_id", ""))
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
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        metadata_={"device_id": payload.device_id, "crop_count": len(payload.crops)},
    ))
    await db.commit()

    # Enqueue Celery task
    _enqueue(str(job_id), payload.model_dump(mode="json"), job.gcs_prefix or "")

    logger.info("job_submitted", job_id=str(job_id), device_id=payload.device_id,
                crop_count=len(payload.crops))

    return SubmitResponse(
        job_id=str(job_id),
        status="queued",
        created_at=now,
        estimated_completion_seconds=_estimate_completion(len(payload.crops)),
    )


def _enqueue(job_id: str, payload: dict, gcs_prefix: str) -> None:
    from services.worker.celery_app import app as celery_app
    celery_app.send_task(
        "services.worker.tasks.inference.run_pipeline",
        args=[job_id, payload, gcs_prefix],
        queue="inference",
    )


async def _check_rate_limit(r, device_id: str) -> None:
    key = f"ratelimit:{device_id}"
    pipe = r.pipeline()
    pipe.incr(key)
    pipe.expire(key, 60)
    count, _ = await pipe.execute()
    if count > settings.rate_limit_per_device:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit exceeded: {settings.rate_limit_per_device} requests/minute per device",
        )
