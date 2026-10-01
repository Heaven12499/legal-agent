"""legal corpus with exact pgvector retrieval

Revision ID: 20261001_0002
Revises: 20260930_0001
"""
from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

revision = "20261001_0002"
down_revision = "20260930_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "legal_documents",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("jurisdiction", sa.String(32), nullable=False),
        sa.Column("document_type", sa.String(32), nullable=False),
        sa.Column("source_url", sa.Text()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "legal_document_versions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("version_label", sa.String(128), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("effective_from", sa.Date()),
        sa.Column("effective_to", sa.Date()),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["legal_documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id", "version_label", name="uq_legal_document_version"),
    )
    op.create_index("ix_legal_document_versions_document_id", "legal_document_versions", ["document_id"])
    op.create_index(
        "idx_legal_version_validity", "legal_document_versions",
        ["status", "effective_from", "effective_to"],
    )
    op.create_table(
        "legal_chunks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("version_id", sa.Integer(), nullable=False),
        sa.Column("chunk_key", sa.String(192), nullable=False),
        sa.Column("chapter", sa.String(256)),
        sa.Column("section", sa.String(256)),
        sa.Column("article_label", sa.String(32), nullable=False),
        sa.Column("article_number", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(512), nullable=False),
        sa.Column("embedding_model", sa.String(128), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.ForeignKeyConstraint(["version_id"], ["legal_document_versions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("version_id", "article_number", name="uq_legal_version_article"),
    )
    op.create_index("ix_legal_chunks_chunk_key", "legal_chunks", ["chunk_key"])
    op.create_index("ix_legal_chunks_version_id", "legal_chunks", ["version_id"])
    op.create_index("idx_legal_chunk_version_article", "legal_chunks", ["version_id", "article_number"])
    # 第一阶段刻意不创建 HNSW/IVFFlat：1,023 条语料使用精确余弦检索，便于与 FAISS 对账。


def downgrade() -> None:
    op.drop_index("idx_legal_chunk_version_article", table_name="legal_chunks")
    op.drop_index("ix_legal_chunks_version_id", table_name="legal_chunks")
    op.drop_index("ix_legal_chunks_chunk_key", table_name="legal_chunks")
    op.drop_table("legal_chunks")
    op.drop_index("idx_legal_version_validity", table_name="legal_document_versions")
    op.drop_index("ix_legal_document_versions_document_id", table_name="legal_document_versions")
    op.drop_table("legal_document_versions")
    op.drop_table("legal_documents")
