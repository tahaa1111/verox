"""
Camera relay — Pi pushes frames via WebSocket, frontend receives them in real-time.

HTTP routes (kept for capture/snapshot fallback):
  POST /camera/push-job    — Pi: push job_id after auto-submit
  POST /camera/capture     — Frontend: manual capture → submit job
  GET  /camera/snapshot    — Frontend: fallback HTTP snapshot (legacy)
  POST /camera/start / stop — Legacy Redis command signaling (kept for fallback)
  GET  /camera/command      — Legacy Pi command poll (kept for fallback)

WebSocket routes (primary path):
  WS /camera/ws/push/{device_id}  — Pi connects, streams frames + stable_progress
  WS /camera/ws/feed/{device_id}  — Browser connects, receives frames live
"""
from __future__ import annotations

import hmac
import json
import uuid
import redis.asyncio as aioredis
import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from services.api.core.auth import verify_firebase_token
from services.api.core.config import get_settings
from services.api.core.database import get_db
from services.api.models.audit import AuditLog
from services.api.models.job import Job
from services.api.ws.camera_relay import camera_relay

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
# arq enqueue helper
# ---------------------------------------------------------------------------

async def _enqueue(request: Request, job_id: str, payload: dict, gcs_prefix: str) -> None:
    arq_pool = request.app.state.arq_pool
    await arq_pool.enqueue_job("run_pipeline", job_id, payload, gcs_prefix, _job_id=job_id)


# ---------------------------------------------------------------------------
# POST /camera/push   (Pi → API)
# ---------------------------------------------------------------------------

@router.post("/camera/push", status_code=204, response_class=Response, include_in_schema=False)
async def camera_push(
    request: Request,
    payload: CameraPushPayload,
    x_camera_secret: str = Header(alias="X-Camera-Secret", default=""),
) -> Response:
    """Pi posts a JPEG frame + stable_progress; stored in Redis."""
    if not hmac.compare_digest(x_camera_secret, settings.camera_secret):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid camera secret")

    r = aioredis.from_url(settings.redis_url, decode_responses=True)
    try:
        # Per-device rate limit: 10 pushes/second max (Pi runs at ~2fps, 10 is generous)
        rl_key = f"rl:campush:{payload.device_id}"
        count = await r.incr(rl_key)
        if count == 1:
            await r.expire(rl_key, 1)  # 1-second window
        if count > 10:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Camera push rate limit exceeded",
                headers={"Retry-After": "1"},
            )
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
    request: Request,
    payload: CameraJobPayload,
    x_camera_secret: str = Header(alias="X-Camera-Secret", default=""),
) -> Response:
    """Pi pushes the job_id it received from /v1/submit so the frontend can navigate to results."""
    if not hmac.compare_digest(x_camera_secret, settings.camera_secret):
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
    # Primary: in-memory relay (WS path); fallback: Redis (legacy HTTP push path)
    frame_b64 = camera_relay.get_latest_frame(device_id)
    if not frame_b64:
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

    user_uid: str = claims.get("uid", claims.get("user_id", ""))
    job = Job(
        id=job_id,
        device_id=device_id,
        session_id=uuid.uuid4(),
        user_uid=user_uid,          # use authenticated user's UID for ownership check
        status="queued",
        crop_count=1,
        gcs_prefix=gcs_prefix,
    )
    db.add(job)
    db.add(AuditLog(
        correlation_id=uuid.uuid4(),
        action="camera_capture_queued",
        actor_uid=user_uid,
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
    await _enqueue(request, str(job_id), payload, gcs_prefix)

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
    if not hmac.compare_digest(x_camera_secret, settings.camera_secret):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid camera secret")
    r = aioredis.from_url(settings.redis_url, decode_responses=True)
    try:
        cmd = await r.getdel(_cmd_key(device_id))
    finally:
        await r.aclose()
    return {"command": cmd or "idle", "device_id": device_id}


# ---------------------------------------------------------------------------
# WebSocket — Pi pushes frames
# ---------------------------------------------------------------------------

@router.websocket("/camera/ws/push/{device_id}")
async def camera_ws_push(
    websocket: WebSocket,
    device_id: str,
    secret: str = "",
) -> None:
    """Pi connects here and streams frames. Auth via ?secret=<CAMERA_SECRET>."""
    if not secret or not hmac.compare_digest(secret, settings.camera_secret):
        await websocket.close(code=4403)
        return

    await camera_relay.connect_pi(device_id, websocket)
    try:
        while True:
            try:
                data = await websocket.receive_text()
                msg = json.loads(data)
                if msg.get("type") == "frame":
                    await camera_relay.relay_frame(device_id, msg)
                elif msg.get("type") == "job_id":
                    # Pi auto-submitted — store job_id in Redis for fallback snapshot
                    r = aioredis.from_url(settings.redis_url, decode_responses=True)
                    try:
                        await r.set(_job_key(device_id), msg.get("job_id", ""), ex=_JOB_REDIS_TTL)
                    finally:
                        await r.aclose()
                    await camera_relay.relay_frame(device_id, msg)
            except WebSocketDisconnect:
                break
    finally:
        camera_relay.disconnect_pi(device_id)


# ---------------------------------------------------------------------------
# WebSocket — Browser receives frames
# ---------------------------------------------------------------------------

@router.websocket("/camera/ws/feed/{device_id}")
async def camera_ws_feed(
    websocket: WebSocket,
    device_id: str,
    token: str = "",
) -> None:
    """Browser connects here to receive live frames. Auth via ?token=<Firebase JWT>."""
    from services.api.core.auth import _get_firebase_app
    from firebase_admin import auth as fb_auth
    try:
        _get_firebase_app()
        fb_auth.verify_id_token(token, check_revoked=True)
    except Exception:
        await websocket.close(code=4401)
        return

    await camera_relay.connect_viewer(device_id, websocket)
    try:
        while True:
            try:
                data = await websocket.receive_text()
                msg = json.loads(data)
                cmd = msg.get("type")
                if cmd in ("start", "stop"):
                    if not await camera_relay.send_command(device_id, cmd):
                        # Pi not connected via WS — fall back to Redis command
                        r = aioredis.from_url(settings.redis_url, decode_responses=True)
                        try:
                            await r.set(_cmd_key(device_id), cmd, ex=_CAMERA_CMD_TTL)
                        finally:
                            await r.aclose()
            except WebSocketDisconnect:
                break
    finally:
        camera_relay.disconnect_viewer(device_id, websocket)
