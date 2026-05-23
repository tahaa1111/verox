"""
Camera relay — Pi pushes frames, frontend polls snapshots and triggers capture.

Routes:
  POST /camera/push        — Pi → API: push base64 JPEG frame + stable_progress
  POST /camera/push-job    — Pi → API: push job_id after auto-submit (stable trigger)
  GET  /camera/snapshot    — Frontend → API: latest frame + stable_progress + latest_job_id
  POST /camera/capture     — Frontend → API: capture current frame → submit job
  POST /camera/start       — Frontend → API → Redis command (Pi picks up to start streaming)
  POST /camera/stop        — Frontend → API → Redis command (Pi picks up to stop streaming)
  GET  /camera/command     — Pi polls for start/stop commands (authenticated by camera secret)
"""
from __future__ import annotations

import uuid
import redis.asyncio as aioredis
import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from services.api.core.auth import verify_firebase_token
from services.api.core.config import get_settings
from services.api.core.database import get_db
from services.api.models.audit import AuditLog
from services.api.models.job import Job

logger = structlog.get_logger(__name__)
router = APIRouter()
settings = get_settings()

_CAMERA_REDIS_TTL = 10        # seconds — frame expires if Pi disconnects
_CAMERA_CMD_TTL = 60          # seconds — command stays until Pi picks it up
_STABLE_REDIS_TTL = 15        # seconds — stable_progress slightly longer TTL
_JOB_REDIS_TTL = 300          # seconds — latest_job_id held 5 min for frontend to pick up
_DEFAULT_DEVICE = "pi-0001"


# ---------------------------------------------------------------------------
# Redis key helpers
# ---------------------------------------------------------------------------

def _frame_key(device_id: str) -> str:
    return f"camera:latest:{device_id}"

def _stable_key(device_id: str) -> str:
    return f"camera:stable:{device_id}"

def _job_key(device_id: str) -> str:
    return f"camera:job:{device_id}"

def _cmd_key(device_id: str) -> str:
    return f"camera:cmd:{device_id}"


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class CameraPushPayload(BaseModel):
    frame: str                      # base64-encoded JPEG
    device_id: str = _DEFAULT_DEVICE
    stable_progress: float = 0.0    # 0.0→1.0, set by Pi stability tracker


class CameraJobPayload(BaseModel):
    job_id: str
    device_id: str = _DEFAULT_DEVICE


class CaptureResponse(BaseModel):
    job_id: str
    device_id: str


# ---------------------------------------------------------------------------
# Celery enqueue helper
# ---------------------------------------------------------------------------

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
    """Pi posts a JPEG frame + stable_progress; stored in Redis."""
    if x_camera_secret != settings.camera_secret:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid camera secret")

    r = aioredis.from_url(settings.redis_url, decode_responses=True)
    try:
        await r.set(_frame_key(payload.device_id), payload.frame, ex=_CAMERA_REDIS_TTL)
        await r.set(
            _stable_key(payload.device_id),
            str(round(payload.stable_progress, 4)),
            ex=_STABLE_REDIS_TTL,
        )
    finally:
        await r.aclose()
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# POST /camera/push-job   (Pi → API, after auto-submit)
# ---------------------------------------------------------------------------

@router.post("/camera/push-job", status_code=204, response_class=Response, include_in_schema=False)
async def camera_push_job(
    payload: CameraJobPayload,
    x_camera_secret: str = Header(alias="X-Camera-Secret", default=""),
) -> Response:
    """Pi pushes the job_id it received from /v1/submit so the frontend can navigate to results."""
    if x_camera_secret != settings.camera_secret:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid camera secret")

    r = aioredis.from_url(settings.redis_url, decode_responses=True)
    try:
        await r.set(_job_key(payload.device_id), payload.job_id, ex=_JOB_REDIS_TTL)
    finally:
        await r.aclose()
    logger.info("camera_job_pushed", job_id=payload.job_id, device_id=payload.device_id)
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# GET /camera/snapshot   (Frontend → API)
# ---------------------------------------------------------------------------

@router.get("/camera/snapshot")
async def camera_snapshot(
    device_id: str = _DEFAULT_DEVICE,
    claims: dict = Depends(verify_firebase_token),
) -> dict:
    """Return the latest frame, stable_progress (0→1), and latest_job_id (if any)."""
    r = aioredis.from_url(settings.redis_url, decode_responses=True)
    try:
        frame, stable_raw, latest_job = await r.mget(
            _frame_key(device_id),
            _stable_key(device_id),
            _job_key(device_id),
        )
    finally:
        await r.aclose()

    if not frame:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No camera feed — make sure the Pi camera is running",
        )

    stable_progress = float(stable_raw) if stable_raw else 0.0

    return {
        "frame": frame,
        "device_id": device_id,
        "stable_progress": round(stable_progress, 4),
        "latest_job_id": latest_job,  # None until Pi auto-submits
    }


# ---------------------------------------------------------------------------
# POST /camera/capture   (Frontend → API — manual capture fallback)
# ---------------------------------------------------------------------------

@router.post("/camera/capture", response_model=CaptureResponse, status_code=202)
async def camera_capture(
    request: Request,
    db: AsyncSession = Depends(get_db),
    device_id: str = _DEFAULT_DEVICE,
    claims: dict = Depends(verify_firebase_token),
) -> CaptureResponse:
    """Capture the latest Pi frame, create a job, return job_id."""
    r = aioredis.from_url(settings.redis_url, decode_responses=True)
    try:
        frame_b64 = await r.get(_frame_key(device_id))
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


# ---------------------------------------------------------------------------
# POST /camera/start  (Frontend → API)
# ---------------------------------------------------------------------------

@router.post("/camera/start", status_code=200)
async def camera_start(
    device_id: str = _DEFAULT_DEVICE,
    claims: dict = Depends(verify_firebase_token),
) -> dict:
    """Signal the Pi camera to start streaming."""
    r = aioredis.from_url(settings.redis_url, decode_responses=True)
    try:
        await r.set(_cmd_key(device_id), "start", ex=_CAMERA_CMD_TTL)
    finally:
        await r.aclose()
    logger.info("camera_start_signaled", device_id=device_id)
    return {"status": "start_signaled", "device_id": device_id}


# ---------------------------------------------------------------------------
# POST /camera/stop  (Frontend → API)
# ---------------------------------------------------------------------------

@router.post("/camera/stop", status_code=200)
async def camera_stop(
    device_id: str = _DEFAULT_DEVICE,
    claims: dict = Depends(verify_firebase_token),
) -> dict:
    """Signal the Pi camera to stop streaming."""
    r = aioredis.from_url(settings.redis_url, decode_responses=True)
    try:
        await r.set(_cmd_key(device_id), "stop", ex=_CAMERA_CMD_TTL)
    finally:
        await r.aclose()
    logger.info("camera_stop_signaled", device_id=device_id)
    return {"status": "stop_signaled", "device_id": device_id}


# ---------------------------------------------------------------------------
# GET /camera/command  (Pi polls every 2 s — authenticated by camera secret)
# ---------------------------------------------------------------------------

@router.get("/camera/command", include_in_schema=False)
async def camera_command(
    device_id: str = _DEFAULT_DEVICE,
    x_camera_secret: str = Header(alias="X-Camera-Secret", default=""),
) -> dict:
    """Pi polls for start/stop commands from the frontend."""
    if x_camera_secret != settings.camera_secret:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid camera secret")
    r = aioredis.from_url(settings.redis_url, decode_responses=True)
    try:
        cmd = await r.getdel(_cmd_key(device_id))
    finally:
        await r.aclose()
    return {"command": cmd or "idle", "device_id": device_id}
