from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, Integer, SmallInteger, Text, TIMESTAMP, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from services.api.models.base import Base


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    device_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    user_uid: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="queued", index=True)
    crop_count: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    gcs_prefix: Mapped[str | None] = mapped_column(Text)
    result: Mapped[dict | None] = mapped_column(JSONB)
    result_schema_version: Mapped[str] = mapped_column(Text, default="1.0")
    error_message: Mapped[str | None] = mapped_column(Text)
    retry_count: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    model_version: Mapped[str | None] = mapped_column(Text)
    vertex_prediction_id: Mapped[str | None] = mapped_column(Text)
    queue_wait_ms: Mapped[int | None] = mapped_column(Integer)
    preprocessing_ms: Mapped[int | None] = mapped_column(Integer)
    inference_ms: Mapped[int | None] = mapped_column(Integer)
    postprocessing_ms: Mapped[int | None] = mapped_column(Integer)
    total_ms: Mapped[int | None] = mapped_column(Integer)
    requires_human_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    review_reasons: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
