"""
Admin-only endpoints: maintenance mode, model rollback.
All require Firebase JWT admin claim + admin_role_grants DB row (DD-013).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from services.api.core.auth import require_admin
from services.api.core.config import get_settings
from services.api.core.database import get_db
from services.api.models.audit import AuditLog
from services.api.schemas.response import MaintenanceModeResponse, ModelRollbackResponse

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/admin", tags=["Admin"])
settings = get_settings()


# ---------------------------------------------------------------------------
# GET /admin/models — model registry list for Admin dashboard
# ---------------------------------------------------------------------------

@router.get("/models")
async def list_models(
    claims: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """Return all model versions from the registry, newest first."""
    from sqlalchemy import select
    from services.api.models.model_registry import ModelRegistry

    result = await db.execute(
        select(ModelRegistry).order_by(ModelRegistry.created_at.desc())
    )
    models = result.scalars().all()

    return [
        {
            "id": str(m.id),
            "display_name": m.version,
            "vertex_model_resource_name": m.vertex_model_resource_name or "",
            "vertex_deployed_model_id": m.vertex_deployed_model_id,
            "eval_drug_f1": float(m.eval_drug_f1) if m.eval_drug_f1 is not None else None,
            "eval_json_validity": float(m.eval_json_validity) if m.eval_json_validity is not None else None,
            "deployed_at": m.deployed_at.isoformat() if m.deployed_at else None,
            "is_current": m.is_active,
        }
        for m in models
    ]


# ---------------------------------------------------------------------------
# POST /admin/maintenance
# ---------------------------------------------------------------------------

class MaintenanceRequest(BaseModel):
    active: bool = False
    reason: str = "System maintenance in progress"


@router.post("/maintenance", response_model=MaintenanceModeResponse)
async def set_maintenance(
    request: Request,
    body: MaintenanceRequest,
    claims: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> MaintenanceModeResponse:
    enable = body.active
    message = body.reason or "System maintenance in progress"
    import redis.asyncio as aioredis
    r = aioredis.from_url(settings.redis_url, decode_responses=True)
    user_uid = claims.get("uid", "")
    try:
        if enable:
            await r.set(settings.maintenance_redis_key, message)
        else:
            await r.delete(settings.maintenance_redis_key)
    finally:
        await r.aclose()

    db.add(AuditLog(
        correlation_id=uuid.uuid4(),
        action=f"maintenance_{'enabled' if enable else 'disabled'}",
        actor_uid=user_uid,
        actor_role="admin",
        resource_type="system",
        resource_id="maintenance",
        ip_address=request.client.host if request and request.client else None,
        metadata_={"enable": enable, "message": message},
    ))
    await db.commit()
    logger.info("maintenance_toggled", enable=enable, by=user_uid)
    return MaintenanceModeResponse(maintenance=enable, toggled_by=user_uid,
                                   timestamp=datetime.now(timezone.utc))


@router.post("/model/rollback", response_model=ModelRollbackResponse)
async def rollback_model(
    request: Request,
    claims: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> ModelRollbackResponse:
    """Mark the previous model version as active in the registry."""
    from sqlalchemy import text

    user_uid = claims.get("uid", "")

    result = await db.execute(
        text("SELECT version FROM model_registry ORDER BY created_at DESC LIMIT 2")
    )
    rows = result.fetchall()
    if len(rows) < 2:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No previous model version to roll back to",
        )
    current_version = rows[0][0]
    previous_version = rows[1][0]

    await db.execute(
        text("UPDATE model_registry SET is_active = false WHERE version = :v"),
        {"v": current_version},
    )
    await db.execute(
        text("UPDATE model_registry SET is_active = true, rollback_reason = :r WHERE version = :v"),
        {"v": previous_version, "r": f"Rolled back from {current_version} by {user_uid}"},
    )
    db.add(AuditLog(
        correlation_id=uuid.uuid4(),
        action="model_rollback",
        actor_uid=user_uid,
        actor_role="admin",
        resource_type="model",
        resource_id=previous_version,
        ip_address=request.client.host if request and request.client else None,
        metadata_={"from": current_version, "to": previous_version},
    ))
    await db.commit()
    logger.warning("model_rolled_back", from_version=current_version,
                   to_version=previous_version, by=user_uid)
    return ModelRollbackResponse(
        previous_version=current_version,
        rolled_back_to=previous_version,
        vertex_traffic_split={},
        timestamp=datetime.now(timezone.utc),
    )
