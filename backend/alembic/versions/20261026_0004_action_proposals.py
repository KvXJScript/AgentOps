"""create action_proposals

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "action_proposals",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tool", sa.String(length=64), nullable=False),
        sa.Column("target", sa.String(length=64), nullable=False),
        sa.Column("arguments", postgresql.JSONB(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("source_type", sa.String(length=16), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("related_issues", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result_issue_id", sa.String(length=32), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'executing', 'executed', 'rejected', 'expired', 'failed')",
            name=op.f("ck_action_proposals_status"),
        ),
        sa.CheckConstraint(
            "status <> 'executed' OR result_issue_id IS NOT NULL",
            name=op.f("ck_action_proposals_executed_has_result"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_action_proposals")),
    )
    op.create_index("ix_action_proposals_created_at", "action_proposals", ["created_at"])
    op.create_index("ix_action_proposals_status", "action_proposals", ["status"])
    op.create_index(
        "uq_action_proposals_pending_source",
        "action_proposals",
        ["source_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index("uq_action_proposals_pending_source", table_name="action_proposals")
    op.drop_index("ix_action_proposals_status", table_name="action_proposals")
    op.drop_index("ix_action_proposals_created_at", table_name="action_proposals")
    op.drop_table("action_proposals")
