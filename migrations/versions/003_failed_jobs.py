"""Add failed_jobs dead-letter table and arq queue index.

Revision ID: 003
Revises: 002
Create Date: 2026-06-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Dead-letter table for OCR jobs that exhausted all retries.
    # App role can INSERT but NOT UPDATE/DELETE (append-only for audit integrity).
    op.create_table(
        "failed_jobs",
        sa.Column("id",               sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("job_id",           sa.UUID(),       nullable=False,    unique=True),
        sa.Column("payload_snapshot", sa.JSON(),       nullable=True,
                  comment="Minimal payload metadata (device_id, crop_count) — no raw images"),
        sa.Column("last_error",       sa.Text(),       nullable=True),
        sa.Column("attempts",         sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column("failed_at",        sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.Column("reviewed_at",      sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_notes",     sa.Text(),       nullable=True),
        comment="Dead-letter queue: jobs that exhausted all arq retries",
    )
    op.create_index("ix_failed_jobs_job_id",   "failed_jobs", ["job_id"])
    op.create_index("ix_failed_jobs_failed_at","failed_jobs", ["failed_at"])

    # Index for arq queue depth monitoring (Prometheus gauge)
    op.create_index(
        "ix_jobs_status_created",
        "jobs",
        ["status", sa.text("created_at DESC")],
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index("ix_jobs_status_created", "jobs")
    op.drop_index("ix_failed_jobs_failed_at", "failed_jobs")
    op.drop_index("ix_failed_jobs_job_id", "failed_jobs")
    op.drop_table("failed_jobs")
