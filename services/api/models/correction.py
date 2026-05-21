from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Text, TIMESTAMP, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from services.api.models.base import Base


class Correction(Base):
    __tablename__ = "corrections"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_uid: Mapped[str] = mapped_column(Text, nullable=False)
    corrected_data: Mapped[dict] = mapped_column(JSONB, nullable=False)
    original_result: Mapped[dict | None] = mapped_column(JSONB)
    correction_reason: Mapped[str | None] = mapped_column(Text)
    used_for_training: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    training_run_id: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
