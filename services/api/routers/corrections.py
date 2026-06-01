"""POST /v1/corrections/{job_id} — submit human corrections for retraining."""

from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.api.core.auth import verify_firebase_token
from services.api.core.database import get_db
from services.api.models.correction import Correction
from services.api.models.job import Job

logger = structlog.get_logger(__name__)
router = APIRouter()


class CorrectionRequest(BaseModel):
    corrected_data: dict
    correction_reason: str | None = None


@router.post("/corrections/{job_id}", status_code=201)
async def submit_correction(
    job_id: str,
    body: CorrectionRequest,
    claims: dict = Depends(verify_firebase_token),
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        uid = uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Job not found")

    result = await db.execute(select(Job).where(Job.id == uid))
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    user_uid = claims.get("uid", claims.get("user_id", ""))
    if job.user_uid != user_uid and not claims.get("admin"):
        raise HTTPException(status_code=403, detail="Access denied")

    correction = Correction(
        job_id=uid,
        user_uid=user_uid,
        corrected_data=body.corrected_data,
        original_result=job.result,
        correction_reason=body.correction_reason,
        used_for_training=False,
    )
    db.add(correction)
    await db.commit()

    try:
        from services.api.core.metrics import CORRECTIONS_TOTAL
        CORRECTIONS_TOTAL.inc()
    except Exception:
        pass

    logger.info("correction_submitted", job_id=job_id, user_uid=user_uid)
    return {
        "correction_id": str(correction.id),
        "status": "accepted",
        "message": "Correction recorded for model training",
    }
