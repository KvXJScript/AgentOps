"""add action_proposals.grounding

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Advisory result of the deterministic grounding check; NULL for proposals drafted before it existed.
    op.add_column("action_proposals", sa.Column("grounding", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("action_proposals", "grounding")
