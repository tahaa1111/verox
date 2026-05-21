from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, Integer, Numeric, SmallInteger, Text, TIMESTAMP, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from services.api.models.base import Base


class ModelRegistry(Base):
    __tablename__ = "model_registry"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    version: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    base_model: Mapped[str] = mapped_column(Text, nullable=False, default="Qwen/Qwen2.5-VL-7B-Instruct")
    gcs_artifact_uri: Mapped[str | None] = mapped_column(Text)           # gs://bucket/path/
    vertex_model_resource_name: Mapped[str | None] = mapped_column(Text) # full Vertex resource name
    vertex_deployed_model_id: Mapped[str | None] = mapped_column(Text)
    pipeline_job_id: Mapped[str | None] = mapped_column(Text)
    container_image_uri: Mapped[str | None] = mapped_column(Text)
    git_sha: Mapped[str | None] = mapped_column(Text)
    training_run_id: Mapped[str | None] = mapped_column(Text)
    training_samples: Mapped[int | None] = mapped_column(Integer)
    training_samples_count: Mapped[int | None] = mapped_column(Integer)
    lora_rank: Mapped[int | None] = mapped_column(SmallInteger)
    training_epochs: Mapped[int | None] = mapped_column(SmallInteger)
    eval_drug_f1: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    eval_drug_class_metrics: Mapped[dict | None] = mapped_column(JSONB)
    eval_rare_drug_accuracy: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    eval_hallucination_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    eval_json_validity: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    eval_json_validity_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    eval_latency_p95_ms: Mapped[int | None] = mapped_column(Integer)
    shadow_eval_disagreement_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    canary_error_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_canary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    canary_traffic_pct: Mapped[int] = mapped_column(SmallInteger, default=0)
    deployed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    retired_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    promotion_reason: Mapped[str | None] = mapped_column(Text)
    rollback_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
