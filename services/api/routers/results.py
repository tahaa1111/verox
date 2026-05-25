"""GET /v1/result/{job_id} + WebSocket /v1/ws/jobs/{job_id}."""

from __future__ import annotations

import asyncio
import json
import secrets
import uuid
from datetime import datetime, timezone

import redis.asyncio as aioredis
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.api.core.auth import verify_firebase_token
from services.api.core.config import get_settings
from services.api.core.database import get_db
from services.api.core.pii import decrypt_pii
from services.api.models.job import Job
from services.api.schemas.response import DISCLAIMER
from services.api.ws.manager import HEARTBEAT_INTERVAL, ws_manager

_WS_TOKEN_TTL = 30        # seconds — opaque token expires after 30s (single-use)
_WS_TOKEN_PREFIX = "wstoken:"
_CANCEL_PREFIX = "cancel:"          # Redis key: cancel:{job_id} → "1"
_CANCEL_TTL = 3600                  # 1 hour — worker checks this key before each retry

logger = structlog.get_logger(__name__)
router = APIRouter()


@router.post("/ws/token", status_code=200)
async def ws_token(
    claims: dict = Depends(verify_firebase_token),
) -> dict:
    """
    Exchange a Firebase JWT for a short-lived (30s) opaque WebSocket token.
    The frontend passes this opaque token in ?token= instead of the full Firebase JWT,
    so the JWT never appears in server logs or browser history.
    """
    settings = get_settings()
    opaque = secrets.token_urlsafe(32)
    r = aioredis.from_url(settings.redis_url, decode_responses=True)
    try:
        await r.set(
            f"{_WS_TOKEN_PREFIX}{opaque}",
            json.dumps({"uid": claims.get("uid", claims.get("user_id", "")),
                        "admin": claims.get("admin", False),
                        "device_id": claims.get("device_id", "")}),
            ex=_WS_TOKEN_TTL,
        )
    finally:
        await r.aclose()
    return {"ws_token": opaque, "expires_in": _WS_TOKEN_TTL}


@router.get("/result/{job_id}")
async def get_result(
    job_id: str,
    claims: dict = Depends(verify_firebase_token),
    db: AsyncSession = Depends(get_db),
):
    job = await _get_owned_job(job_id, claims, db)

    if job.status in ("queued", "preprocessing", "inferring", "postprocessing", "retrying"):
        progress_map = {"queued": 5, "preprocessing": 20, "inferring": 60,
                        "postprocessing": 85, "retrying": 10}
        return {
            "job_id": str(job.id),
            "status": job.status,
            "progress_pct": progress_map.get(job.status, 0),
            "estimated_completion_s": None,
            "result": None,
            "error_message": None,
        }

    if job.status == "failed":
        return {
            "job_id": str(job.id),
            "status": "failed",
            "progress_pct": 0,
            "estimated_completion_s": None,
            "result": None,
            "error_message": job.error_message,
        }

    # Completed — decrypt PII and wrap in a consistent JobPollResponse envelope
    result = dict(job.result or {})
    if result.get("patient_name"):
        result["patient_name"] = decrypt_pii(result["patient_name"])
    if result.get("doctor_name"):
        result["doctor_name"] = decrypt_pii(result["doctor_name"])
    # Also decrypt PII inside the nested patient / doctor objects (new schema)
    if result.get("patient") and isinstance(result["patient"], dict):
        p = dict(result["patient"])
        if p.get("name"):
            p["name"] = decrypt_pii(p["name"])
        if p.get("last_name"):
            p["last_name"] = decrypt_pii(p["last_name"])
        result["patient"] = p
    if result.get("doctor") and isinstance(result["doctor"], dict):
        d = dict(result["doctor"])
        if d.get("name"):
            d["name"] = decrypt_pii(d["name"])
        result["doctor"] = d
    result["disclaimer"] = DISCLAIMER
    return {
        "job_id": str(job.id),
        "status": "completed",
        "progress_pct": 100,
        "estimated_completion_s": None,
        "result": result,
        "error_message": None,
    }


@router.post("/jobs/{job_id}/cancel", status_code=200)
async def cancel_job(
    job_id: str,
    claims: dict = Depends(verify_firebase_token),
    db: AsyncSession = Depends(get_db),
):
    """
    Cancel a queued or in-progress job.
    Sets a Redis cancellation flag that the worker checks before each vLLM retry.
    Also marks the job status as 'cancelled' in Postgres immediately.
    """
    job = await _get_owned_job(job_id, claims, db)

    # Only cancel jobs that are not already terminal
    if job.status in ("completed", "failed", "cancelled"):
        return {"job_id": job_id, "status": job.status, "message": "Job already in terminal state."}

    # 1. Set Redis cancellation signal — worker checks this before each vLLM retry
    settings = get_settings()
    r = aioredis.from_url(settings.redis_url, decode_responses=True)
    try:
        await r.set(f"{_CANCEL_PREFIX}{job_id}", "1", ex=_CANCEL_TTL)
    finally:
        await r.aclose()

    # 2. Mark job as cancelled in Postgres
    job.status = "cancelled"
    job.error_message = "Cancelled by user."
    await db.commit()

    # 3. Notify any open WebSocket listeners
    await ws_manager.send_to_job(job_id, {
        "event": "cancelled",
        "job_id": job_id,
        "ts": datetime.now(timezone.utc).isoformat(),
    })

    logger.info("job_cancelled", job_id=job_id,
                uid=claims.get("uid", claims.get("user_id", "")))
    return {"job_id": job_id, "status": "cancelled"}


@router.websocket("/ws/jobs/{job_id}")
async def websocket_result(
    websocket: WebSocket,
    job_id: str,
    token: str = Query(...),
):
    # Authenticate via short-lived opaque token (Redis lookup, single-use).
    # Frontend must first call POST /v1/ws/token to exchange Firebase JWT → opaque token.
    settings = get_settings()
    r = aioredis.from_url(settings.redis_url, decode_responses=True)
    try:
        key = f"{_WS_TOKEN_PREFIX}{token}"
        raw = await r.getdel(key)   # single-use: deleted on first read
    finally:
        await r.aclose()

    if not raw:
        await websocket.close(code=4001)
        return
    try:
        claims = json.loads(raw)
    except Exception:
        await websocket.close(code=4001)
        return

    # Verify ownership
    from services.api.core.database import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        try:
            job = await _get_owned_job(job_id, claims, db)
        except HTTPException:
            await websocket.close(code=4003)
            return

        # Send current state immediately
        current_event: dict = {"event": "status", "job_id": job_id, "status": job.status,
                               "ts": datetime.now(timezone.utc).isoformat()}
        if job.status == "completed" and job.result:
            result = dict(job.result)
            if result.get("patient_name"):
                result["patient_name"] = decrypt_pii(result["patient_name"])
            if result.get("doctor_name"):
                result["doctor_name"] = decrypt_pii(result["doctor_name"])
            # Also decrypt PII inside the nested patient / doctor objects (new schema)
            if result.get("patient") and isinstance(result["patient"], dict):
                p = dict(result["patient"])
                if p.get("name"):
                    p["name"] = decrypt_pii(p["name"])
                if p.get("last_name"):
                    p["last_name"] = decrypt_pii(p["last_name"])
                result["patient"] = p
            if result.get("doctor") and isinstance(result["doctor"], dict):
                d = dict(result["doctor"])
                if d.get("name"):
                    d["name"] = decrypt_pii(d["name"])
                result["doctor"] = d
            result["disclaimer"] = DISCLAIMER
            current_event = {"event": "completed", "job_id": job_id, "result": result,
                             "ts": datetime.now(timezone.utc).isoformat()}

    await ws_manager.connect(job_id, websocket)
    try:
        await websocket.send_json(current_event)
        # Heartbeat loop — keeps Cloud Run connection alive (DD-012)
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            try:
                await websocket.send_json({"event": "heartbeat",
                                           "ts": datetime.now(timezone.utc).isoformat()})
            except Exception:
                break
    except WebSocketDisconnect:
        pass
    finally:
        ws_manager.disconnect(job_id, websocket)


async def _get_owned_job(job_id: str, claims: dict, db: AsyncSession) -> Job:
    try:
        uid = uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    result = await db.execute(select(Job).where(Job.id == uid))
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    user_uid = claims.get("uid", claims.get("user_id", ""))
    if job.user_uid != user_uid and not claims.get("admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    return job
