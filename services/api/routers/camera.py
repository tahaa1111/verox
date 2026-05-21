"""
Camera relay — Pi pushes frames, frontend polls snapshots and triggers capture.

Routes:
  POST /camera/push        — Pi → API (device pushes base64 JPEG frames via X-Camera-Secret)
  GET  /camera/snapshot    — Frontend → API (get latest frame from Redis)
  POST /camera/capture     — Frontend → API (capture current frame → submit job)
"""
from __future__ import annotations

import uuid
import redis.asyncio as aioredis
import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from services.api.core.config import get_settings
from services.api.core.database import get_db
from services.api.models.audit import AuditLog
from services.api.models.job import Job

logger = structlog.get_logger(__name__)
router = APIRouter()
settings = get_settings()

_CAMERA_REDIS_TTL = 10        # seconds — frame expires if Pi disconnects
_DEFAULT_DEVICE = "pi-0001"


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class CameraPushPayload(BaseModel):
    frame: str          # base64-encoded JPEG
    device_id: str = _DEFAULT_DEVICE


class CaptureResponse(BaseModel):
    job_id: str
    device_id: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _redis_key(device_id: str) -> str:
    return f"camera:latest:{device_id}"


def _enqueue(job_id: str, payload: dict, gcs_prefix: str) -> None:
    from services.worker.celery_app import app as celery_app
    celery_app.send_task(
        "services.worker.tasks.inference.run_pipeline",
        args=[job_id, payload, gcs_prefix],
        queue="inference",
    )


# ---------------------------------------------------------------------------
# POST /camera/push   (Pi → API)
# ---------------------------------------------------------------------------

@router.post("/camera/push", status_code=204, response_class=Response, include_in_schema=False)
async def camera_push(
    payload: CameraPushPayload,
    x_camera_secret: str = Header(alias="X-Camera-Secret", default=""),
) -> Response:
    """Pi posts a JPEG frame; stored in Redis for 10 s. No auth required if secret matches."""
    if x_camera_secret != settings.camera_secret:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid camera secret")

    r = aioredis.from_url(settings.redis_url, decode_responses=True)
    try:
        await r.set(_redis_key(payload.device_id), payload.frame, ex=_CAMERA_REDIS_TTL)
    finally:
        await r.aclose()
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# GET /camera/snapshot   (Frontend → API)
# ---------------------------------------------------------------------------

@router.get("/camera/snapshot")
async def camera_snapshot(device_id: str = _DEFAULT_DEVICE) -> dict:
    """Return the latest frame from Redis. No auth — frame is just a JPEG crop."""
    r = aioredis.from_url(settings.redis_url, decode_responses=True)
    try:
        frame = await r.get(_redis_key(device_id))
    finally:
        await r.aclose()

    if not frame:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No camera feed — make sure the Pi camera is running",
        )
    return {"frame": frame, "device_id": device_id}


# ---------------------------------------------------------------------------
# POST /camera/capture   (Frontend → API)
# ---------------------------------------------------------------------------

@router.post("/camera/capture", response_model=CaptureResponse, status_code=202)
async def camera_capture(
    request: Request,
    db: AsyncSession = Depends(get_db),
    device_id: str = _DEFAULT_DEVICE,
) -> CaptureResponse:
    """Capture the latest Pi frame, create a job, return job_id."""
    r = aioredis.from_url(settings.redis_url, decode_responses=True)
    try:
        frame_b64 = await r.get(_redis_key(device_id))
    finally:
        await r.aclose()

    if not frame_b64:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No live camera feed available — start the Pi camera first",
        )

    job_id = uuid.uuid4()
    gcs_prefix = f"{device_id}/{job_id}"

    job = Job(
        id=job_id,
        device_id=device_id,
        session_id=uuid.uuid4(),
        user_uid="camera-capture",
        status="queued",
        crop_count=1,
        gcs_prefix=gcs_prefix,
    )
    db.add(job)
    db.add(AuditLog(
        correlation_id=uuid.uuid4(),
        action="camera_capture_queued",
        actor_uid="camera-ui",
        resource_type="job",
        resource_id=str(job_id),
        ip_address=request.client.host if request.client else None,
        metadata_={"device_id": device_id},
    ))
    await db.commit()

    # Enqueue Celery task — same pattern as /v1/submit
    payload = {
        "crops": [
            {
                "image_base64": frame_b64,
                "bbox": [0.0, 0.0, 1.0, 1.0],
                "confidence": 1.0,
                "track_id": 0,
            }
        ],
        "device_id": device_id,
        "session_id": str(job_id),
    }
    _enqueue(str(job_id), payload, gcs_prefix)

    logger.info("camera_capture_queued", job_id=str(job_id), device_id=device_id)
    return CaptureResponse(job_id=str(job_id), device_id=device_id)
