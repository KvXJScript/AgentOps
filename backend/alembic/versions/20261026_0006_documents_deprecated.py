"""add documents.deprecated (front matter `status: deprecated`, demoted at retrieval)

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing documents count as current until they are re-ingested (retry), which parses their front matter.
    op.add_column("documents", sa.Column("deprecated", sa.Boolean(), server_default=sa.false(), nullable=False))


def downgrade() -> None:
    op.drop_column("documents", "deprecated")
