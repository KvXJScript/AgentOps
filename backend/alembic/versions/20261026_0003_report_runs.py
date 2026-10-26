"""create report_runs

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "report_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("document_ids", postgresql.JSONB(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("stages", postgresql.JSONB(), nullable=False),
        sa.Column("report", postgresql.JSONB(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'insufficient_evidence', 'failed')",
            name=op.f("ck_report_runs_status"),
        ),
        sa.CheckConstraint(
            "status = 'succeeded' OR report IS NULL", name=op.f("ck_report_runs_report_only_when_succeeded")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_report_runs")),
    )
    op.create_index("ix_report_runs_created_at", "report_runs", ["created_at"])
    op.create_index("ix_report_runs_status", "report_runs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_report_runs_status", table_name="report_runs")
    op.drop_index("ix_report_runs_created_at", table_name="report_runs")
    op.drop_table("report_runs")
