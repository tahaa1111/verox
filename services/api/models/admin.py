from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Integer, SmallInteger, Text, TIMESTAMP, func
from sqlalchemy.orm import Mapped, mapped_column

from services.api.models.base import Base


class AdminRoleGrant(Base):
    """Second auth factor for admin endpoints (DD-013). Append-only."""
    __tablename__ = "admin_role_grants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)  # Firebase UID
    granted_by: Mapped[str] = mapped_column(Text, nullable=False)
    granted_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    revoked_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    revoked_by: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)


class DeploymentLog(Base):
    """Audit trail for Vertex AI endpoint actions."""
    __tablename__ = "deployment_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    model_version: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    vertex_model_id: Mapped[str | None] = mapped_column(Text)
    vertex_endpoint_id: Mapped[str | None] = mapped_column(Text)
    deployed_model_id: Mapped[str | None] = mapped_column(Text)
    action: Mapped[str] = mapped_column(Text, nullable=False)  # deploy|rollback|undeploy|traffic_split
    traffic_pct: Mapped[int | None] = mapped_column(SmallInteger)
    previous_model_version: Mapped[str | None] = mapped_column(Text)
    pipeline_run_id: Mapped[str | None] = mapped_column(Text)
    triggered_by: Mapped[str | None] = mapped_column(Text)  # pipeline|admin_api|manual
    actor_uid: Mapped[str | None] = mapped_column(Text)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now(), index=True
    )
