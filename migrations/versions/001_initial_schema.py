"""initial schema

Revision ID: 001
Revises:
Create Date: 2026-05-18 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # jobs
    op.create_table(
        "jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("device_id", sa.Text(), nullable=False),
        sa.Column("user_uid", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="queued"),
        sa.Column("crop_count", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("minio_prefix", sa.Text()),
        sa.Column("result", postgresql.JSONB()),
        sa.Column("result_schema_version", sa.Text(), server_default="1.0"),
        sa.Column("error_message", sa.Text()),
        sa.Column("retry_count", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("model_version", sa.Text()),
        sa.Column("queue_wait_ms", sa.Integer()),
        sa.Column("preprocessing_ms", sa.Integer()),
        sa.Column("inference_ms", sa.Integer()),
        sa.Column("postprocessing_ms", sa.Integer()),
        sa.Column("total_ms", sa.Integer()),
        sa.Column("requires_human_review", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("review_reasons", postgresql.ARRAY(sa.Text())),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True)),
    )
    op.create_index("idx_jobs_status", "jobs", ["status"])
    op.create_index("idx_jobs_device_id", "jobs", ["device_id"])
    op.create_index("idx_jobs_created_at", "jobs", [sa.text("created_at DESC")])
    op.create_index("idx_jobs_user_uid", "jobs", ["user_uid"])

    # corrections
    op.create_table(
        "corrections",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_uid", sa.Text(), nullable=False),
        sa.Column("corrected_data", postgresql.JSONB(), nullable=False),
        sa.Column("original_result", postgresql.JSONB()),
        sa.Column("correction_reason", sa.Text()),
        sa.Column("used_for_training", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("training_run_id", sa.Text()),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index("idx_corrections_job_id", "corrections", ["job_id"])
    op.create_index("idx_corrections_training", "corrections", ["used_for_training", sa.text("created_at DESC")])

    # audit_log
    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("correlation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("actor_uid", sa.Text()),
        sa.Column("actor_role", sa.Text()),
        sa.Column("resource_type", sa.Text()),
        sa.Column("resource_id", sa.Text()),
        sa.Column("payload_hash", sa.Text()),
        sa.Column("ip_address", postgresql.INET()),
        sa.Column("user_agent", sa.Text()),
        sa.Column("metadata", postgresql.JSONB()),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index("idx_audit_correlation", "audit_log", ["correlation_id"])
    op.create_index("idx_audit_actor", "audit_log", ["actor_uid", sa.text("created_at DESC")])

    # model_registry
    op.create_table(
        "model_registry",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("version", sa.Text(), unique=True, nullable=False),
        sa.Column("base_model", sa.Text(), nullable=False, server_default="Qwen/Qwen2.5-VL-7B-Instruct"),
        sa.Column("adapter_minio_path", sa.Text()),
        sa.Column("git_sha", sa.Text()),
        sa.Column("training_run_id", sa.Text()),
        sa.Column("training_samples", sa.Integer()),
        sa.Column("eval_drug_f1", sa.Numeric(5, 4)),
        sa.Column("eval_hallucination_rate", sa.Numeric(5, 4)),
        sa.Column("eval_json_validity", sa.Numeric(5, 4)),
        sa.Column("eval_latency_p95_ms", sa.Integer()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_canary", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("canary_traffic_pct", sa.SmallInteger(), server_default="0"),
        sa.Column("deployed_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("retired_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("promotion_reason", sa.Text()),
        sa.Column("rollback_reason", sa.Text()),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )


def downgrade() -> None:
    op.drop_table("model_registry")
    op.drop_table("audit_log")
    op.drop_table("corrections")
    op.drop_table("jobs")
