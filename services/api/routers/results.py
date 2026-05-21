"""GET /v1/result/{job_id} + WebSocket /v1/ws/jobs/{job_id}."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.api.core.auth import get_current_user_ws, verify_firebase_token
from services.api.core.database import get_db
from services.api.core.pii import decrypt_pii
from services.api.models.job import Job
from services.api.schemas.response import DISCLAIMER, JobPollResponse
from services.api.ws.manager import HEARTBEAT_INTERVAL, ws_manager

logger = structlog.get_logger(__name__)
router = APIRouter()


@router.get("/result/{job_id}")
async def get_result(
    job_id: str,
    claims: dict = Depends(verify_firebase_token),
    db: AsyncSession = Depends(get_db),
):
    job = await _get_owned_job(job_id, claims, db)

    if job.status in ("queued", "preprocessing", "inferring", "postprocessing", "retrying"):
        progress_map = {"queued": 0.05, "preprocessing": 0.2, "inferring": 0.6,
                        "postprocessing": 0.85, "retrying": 0.1}
        return JobPollResponse(
            job_id=str(job.id),
            status=job.status,
            progress=progress_map.get(job.status, 0.0),
        )

    if job.status == "failed":
        return JobPollResponse(
            job_id=str(job.id),
            status="failed",
            progress=0.0,
            message=job.error_message,
        )

    # Completed — decrypt PII before returning
    result = dict(job.result or {})
    if result.get("patient_name"):
        result["patient_name"] = decrypt_pii(result["patient_name"])
    if result.get("doctor_name"):
        result["doctor_name"] = decrypt_pii(result["doctor_name"])
    result["disclaimer"] = DISCLAIMER
    return result


@router.websocket("/ws/jobs/{job_id}")
async def websocket_result(
    websocket: WebSocket,
    job_id: str,
    token: str = Query(...),
):
    # Authenticate
    try:
        claims = await get_current_user_ws(token)
    except HTTPException:
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
