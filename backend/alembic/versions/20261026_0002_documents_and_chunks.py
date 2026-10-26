"""documents, chunks (pgvector), message citations and retrieval traces

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Must match agentops.infrastructure.db.documents.EMBEDDING_DIMENSIONS.
EMBEDDING_DIMENSIONS = 384


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("media_type", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("checksum_sha256", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("chunk_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("embedding_model", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('pending', 'processing', 'ready', 'failed')", name=op.f("ck_documents_status")),
        sa.CheckConstraint(
            "media_type IN ('text/markdown', 'text/plain', 'application/pdf')", name=op.f("ck_documents_media_type")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_documents")),
        sa.UniqueConstraint("checksum_sha256", name=op.f("uq_documents_checksum_sha256")),
    )
    op.create_index("ix_documents_created_at", "documents", ["created_at"])

    # Original bytes, kept apart so listing documents never loads them; needed for retry.
    op.create_table(
        "document_sources",
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_document_sources_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("document_id", name=op.f("pk_document_sources")),
    )

    op.create_table(
        "document_chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("char_start", sa.Integer(), nullable=False),
        sa.Column("char_end", sa.Integer(), nullable=False),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("heading_path", postgresql.JSONB(), server_default="[]", nullable=False),
        sa.Column("embedding", Vector(EMBEDDING_DIMENSIONS), nullable=False),
        sa.Column("embedding_model", sa.String(length=128), nullable=False),
        sa.CheckConstraint("char_end > char_start", name=op.f("ck_document_chunks_offsets")),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_document_chunks_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_chunks")),
        sa.UniqueConstraint("document_id", "ordinal", name=op.f("uq_document_chunks_document_id")),
    )
    op.execute(
        "CREATE INDEX ix_document_chunks_embedding ON document_chunks "
        "USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)"
    )

    # Citations are snapshots (title, excerpt, location), so they outlive the chunks they point to.
    op.add_column("messages", sa.Column("grounding", sa.String(length=32), server_default="none", nullable=False))
    op.add_column(
        "messages", sa.Column("citations", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False)
    )
    op.create_check_constraint(
        op.f("ck_messages_grounding"), "messages", "grounding IN ('none', 'grounded', 'insufficient_evidence')"
    )

    op.create_table(
        "message_retrievals",
        sa.Column("message_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("top_k", sa.Integer(), nullable=False),
        sa.Column("min_score", sa.Float(), nullable=False),
        sa.Column("embedding_model", sa.String(length=128), nullable=False),
        sa.Column("chunks", postgresql.JSONB(), nullable=False),
        sa.Column("cited_chunk_ids", postgresql.JSONB(), nullable=False),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["messages.id"],
            name=op.f("fk_message_retrievals_message_id_messages"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("message_id", name=op.f("pk_message_retrievals")),
    )


def downgrade() -> None:
    op.drop_table("message_retrievals")
    op.drop_constraint(op.f("ck_messages_grounding"), "messages", type_="check")
    op.drop_column("messages", "citations")
    op.drop_column("messages", "grounding")
    op.execute("DROP INDEX IF EXISTS ix_document_chunks_embedding")
    op.drop_table("document_chunks")
    op.drop_table("document_sources")
    op.drop_index("ix_documents_created_at", table_name="documents")
    op.drop_table("documents")
