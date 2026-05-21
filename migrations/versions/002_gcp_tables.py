"""GCP-specific tables: admin_role_grants, deployment_log; update model_registry for Vertex AI

Revision ID: 002
Revises: 001
Create Date: 2026-05-20
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # admin_role_grants — second auth factor for admin endpoints (DD-013)
    # Append-only: rows are never hard-deleted (revoked_at is set instead)
    # ------------------------------------------------------------------
    op.create_table(
        "admin_role_grants",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("user_id", sa.Text(), nullable=False),   # Firebase UID
        sa.Column("granted_by", sa.Text(), nullable=False),
        sa.Column("granted_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.Column("revoked_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("revoked_by", sa.Text()),
        sa.Column("notes", sa.Text()),
    )
    op.create_index("idx_admin_grants_user", "admin_role_grants", ["user_id"])
    op.create_index("idx_admin_grants_active", "admin_role_grants",
                    ["user_id", sa.text("revoked_at NULLS FIRST")])

    # ------------------------------------------------------------------
    # deployment_log — tracks Vertex AI endpoint deploys, traffic splits,
    # and rollbacks for audit and pipeline lineage
    # ------------------------------------------------------------------
    op.create_table(
        "deployment_log",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("model_version", sa.Text(), nullable=False),
        sa.Column("vertex_model_id", sa.Text()),           # Vertex AI model resource name
        sa.Column("vertex_endpoint_id", sa.Text()),
        sa.Column("deployed_model_id", sa.Text()),         # Vertex AI deployedModel ID
        sa.Column("action", sa.Text(), nullable=False),    # deploy | rollback | undeploy | traffic_split
        sa.Column("traffic_pct", sa.SmallInteger()),       # 0-100
        sa.Column("previous_model_version", sa.Text()),
        sa.Column("pipeline_run_id", sa.Text()),           # Vertex AI Pipelines run ID
        sa.Column("triggered_by", sa.Text()),              # pipeline | admin_api | manual
        sa.Column("actor_uid", sa.Text()),
        sa.Column("success", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("error_message", sa.Text()),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
    )
    op.create_index("idx_deployment_log_version", "deployment_log", ["model_version"])
    op.create_index("idx_deployment_log_created", "deployment_log",
                    [sa.text("created_at DESC")])

    # ------------------------------------------------------------------
    # Update model_registry: replace minio_path with GCS path + Vertex IDs
    # ------------------------------------------------------------------
    # Rename the old MinIO column to GCS equivalent
    op.add_column("model_registry",
        sa.Column("gcs_artifact_uri", sa.Text()))              # gs://bucket/path/
    op.add_column("model_registry",
        sa.Column("vertex_model_resource_name", sa.Text()))    # full Vertex resource name
    op.add_column("model_registry",
        sa.Column("vertex_deployed_model_id", sa.Text()))      # current deployedModel on endpoint
    op.add_column("model_registry",
        sa.Column("pipeline_job_id", sa.Text()))               # Vertex Pipelines job that created it
    op.add_column("model_registry",
        sa.Column("eval_drug_class_metrics", postgresql.JSONB()))  # per-class F1 scores
    op.add_column("model_registry",
        sa.Column("eval_rare_drug_accuracy", sa.Numeric(5, 4)))
    op.add_column("model_registry",
        sa.Column("eval_json_validity_rate", sa.Numeric(5, 4)))
    op.add_column("model_registry",
        sa.Column("shadow_eval_disagreement_rate", sa.Numeric(5, 4)))
    op.add_column("model_registry",
        sa.Column("canary_error_rate", sa.Numeric(5, 4)))
    op.add_column("model_registry",
        sa.Column("container_image_uri", sa.Text()))           # full image URI with digest
    op.add_column("model_registry",
        sa.Column("lora_rank", sa.SmallInteger()))
    op.add_column("model_registry",
        sa.Column("training_epochs", sa.SmallInteger()))
    op.add_column("model_registry",
        sa.Column("training_samples_count", sa.Integer()))

    # Drop on-prem MinIO path column
    op.drop_column("model_registry", "adapter_minio_path")


def downgrade() -> None:
    op.add_column("model_registry",
        sa.Column("adapter_minio_path", sa.Text()))
    op.drop_column("model_registry", "training_samples_count")
    op.drop_column("model_registry", "training_epochs")
    op.drop_column("model_registry", "lora_rank")
    op.drop_column("model_registry", "container_image_uri")
    op.drop_column("model_registry", "canary_error_rate")
    op.drop_column("model_registry", "shadow_eval_disagreement_rate")
    op.drop_column("model_registry", "eval_json_validity_rate")
    op.drop_column("model_registry", "eval_rare_drug_accuracy")
    op.drop_column("model_registry", "eval_drug_class_metrics")
    op.drop_column("model_registry", "pipeline_job_id")
    op.drop_column("model_registry", "vertex_deployed_model_id")
    op.drop_column("model_registry", "vertex_model_resource_name")
    op.drop_column("model_registry", "gcs_artifact_uri")
    op.drop_table("deployment_log")
    op.drop_table("admin_role_grants")
