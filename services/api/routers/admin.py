"""
Admin-only endpoints: maintenance mode, model rollback.
All require Firebase JWT admin claim + admin_role_grants DB row (DD-013).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from services.api.core.auth import require_admin
from services.api.core.config import get_settings
from services.api.core.database import get_db
from services.api.models.admin import DeploymentLog
from services.api.models.audit import AuditLog
from services.api.schemas.response import MaintenanceModeResponse, ModelRollbackResponse

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/admin", tags=["Admin"])
settings = get_settings()


@router.post("/maintenance", response_model=MaintenanceModeResponse)
async def set_maintenance(
    request: Request,
    enable: bool,
    message: str = "System maintenance in progress",
    claims: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> MaintenanceModeResponse:
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
    """
    Flip Vertex AI endpoint trafficSplit to the previous deployedModel.
    Sub-second effect via Vertex AI API.
    """
    from sqlalchemy import text

    user_uid = claims.get("uid", "")

    # Find current active + previous version from model_registry
    result = await db.execute(
        text(
            "SELECT version, vertex_deployed_model_id FROM model_registry "
            "ORDER BY created_at DESC LIMIT 2"
        )
    )
    rows = result.fetchall()
    if len(rows) < 2:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No previous model version to roll back to",
        )
    current_version, current_deployed_id = rows[0]
    previous_version, previous_deployed_id = rows[1]

    if not settings.vertex_endpoint_id:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="VERTEX_ENDPOINT_ID not configured",
        )

    # Flip traffic split via Vertex AI API
    traffic_split = await _flip_vertex_traffic(
        endpoint_id=settings.vertex_endpoint_id,
        region=settings.vertex_endpoint_region,
        project=settings.gcp_project_id,
        deployed_model_id=previous_deployed_id,
    )

    # Update model_registry
    await db.execute(
        text("UPDATE model_registry SET is_active = false WHERE version = :v"),
        {"v": current_version},
    )
    await db.execute(
        text("UPDATE model_registry SET is_active = true, rollback_reason = :r "
             "WHERE version = :v"),
        {"v": previous_version, "r": f"Rolled back from {current_version} by {user_uid}"},
    )
    db.add(DeploymentLog(
        model_version=previous_version,
        vertex_endpoint_id=settings.vertex_endpoint_id,
        deployed_model_id=previous_deployed_id,
        action="rollback",
        traffic_pct=100,
        previous_model_version=current_version,
        triggered_by="admin_api",
        actor_uid=user_uid,
        success=True,
    ))
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
        vertex_traffic_split=traffic_split,
        timestamp=datetime.now(timezone.utc),
    )


async def _flip_vertex_traffic(
    endpoint_id: str, region: str, project: str, deployed_model_id: str
) -> dict[str, int]:
    """Update Vertex AI endpoint trafficSplit to route 100% to deployed_model_id."""
    try:
        from google.cloud import aiplatform
        aiplatform.init(project=project, location=region)
        endpoint = aiplatform.Endpoint(endpoint_name=endpoint_id)
        traffic_split = {deployed_model_id: 100}
        endpoint.update(traffic_split=traffic_split)
        return traffic_split
    except Exception as exc:
        logger.error("vertex_traffic_flip_failed", exc=str(exc))
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Vertex AI traffic flip failed: {exc}",
        )
