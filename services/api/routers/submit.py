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

    r = aioredis.from_url(settings.redis_url, decode_responses=True)
    try:
        # 1. Check IP soft-block (abuse detection)
        await check_ip_blocked(r, ip)

        # 2. Check maintenance mode
        maintenance = await r.get(settings.maintenance_redis_key)
        if maintenance:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"status": "maintenance", "message": maintenance},
            )

        # 3. Verify device_id claim matches payload
        verify_device_claim(claims, payload.device_id)

        # 4. Multi-layer rate limiting (per-IP + per-user + per-device + burst)
        await check_rate_limits(r, ip=ip, user_id=user_uid, device_id=payload.device_id)

    finally:
        await r.aclose()

    # 5. Create job + audit log
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
        },
    ))
    await db.commit()

    # 6. Enqueue Celery task
    _enqueue(str(job_id), payload.model_dump(mode="json"), job.gcs_prefix or "")

    # 7. Prometheus counter
    try:
        from services.api.core.metrics import OCR_JOBS_TOTAL
        OCR_JOBS_TOTAL.labels(status="queued").inc()
    except Exception:
        pass

    logger.info("job_submitted", job_id=str(job_id), device_id=payload.device_id,
                crop_count=len(payload.crops), user_uid=user_uid)

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
