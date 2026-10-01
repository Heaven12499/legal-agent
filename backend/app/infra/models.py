# -*- coding: utf-8 -*-
"""SQLAlchemy 领域持久化模型。"""
from datetime import date
from sqlalchemy import (
    Boolean, Date, Float, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from pgvector.sqlalchemy import Vector

from .database import Base


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[float] = mapped_column(Float, nullable=False)


class ChatSession(Base):
    __tablename__ = "sessions"
    session_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[float] = mapped_column(Float, nullable=False)
    updated_at: Mapped[float] = mapped_column(Float, nullable=False, index=True)
    contract: Mapped[str | None] = mapped_column(Text)
    contract_name: Mapped[str | None] = mapped_column(String(512))


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("sessions.session_id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    citation: Mapped[dict | None] = mapped_column(JSON)
    trace: Mapped[list | None] = mapped_column(JSON)
    # 每个审查任务最多落一条助手消息，保证 RabbitMQ 至少一次投递下的幂等。
    review_job_id: Mapped[str | None] = mapped_column(String(64), unique=True)


class ReviewJob(Base):
    __tablename__ = "review_jobs"
    job_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("sessions.session_id", ondelete="CASCADE"), nullable=False
    )
    user_message_id: Mapped[int] = mapped_column(ForeignKey("messages.id"), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="queued")
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    phase: Mapped[str] = mapped_column(String(64), nullable=False, default="queued")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    result: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    worker_id: Mapped[str | None] = mapped_column(String(128))
    lease_expires_at: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[float] = mapped_column(Float, nullable=False)
    updated_at: Mapped[float] = mapped_column(Float, nullable=False)
    started_at: Mapped[float | None] = mapped_column(Float)
    finished_at: Mapped[float | None] = mapped_column(Float)
    __table_args__ = (
        Index("idx_review_jobs_user_created", "user_id", "created_at"),
        Index("idx_review_jobs_status_updated", "status", "updated_at"),
    )


class TaskOutbox(Base):
    __tablename__ = "task_outbox"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("review_jobs.job_id", ondelete="CASCADE"), nullable=False
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    published: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    claimed_at: Mapped[float | None] = mapped_column(Float)
    publisher_id: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[float] = mapped_column(Float, nullable=False)
    published_at: Mapped[float | None] = mapped_column(Float)
    __table_args__ = (UniqueConstraint("job_id", "attempt", name="uq_outbox_job_attempt"),)


class LegalDocument(Base):
    """法律文件的稳定身份；具体修订版本单独建表，避免把版本字段复制到每条 chunk。"""
    __tablename__ = "legal_documents"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    jurisdiction: Mapped[str] = mapped_column(String(32), nullable=False, default="CN")
    document_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text)


class LegalDocumentVersion(Base):
    __tablename__ = "legal_document_versions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("legal_documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_label: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="active")
    effective_from: Mapped[date | None] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    __table_args__ = (
        UniqueConstraint("document_id", "version_label", name="uq_legal_document_version"),
        Index("idx_legal_version_validity", "status", "effective_from", "effective_to"),
    )


class LegalChunk(Base):
    __tablename__ = "legal_chunks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    version_id: Mapped[int] = mapped_column(
        ForeignKey("legal_document_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    chunk_key: Mapped[str] = mapped_column(String(192), nullable=False)
    chapter: Mapped[str | None] = mapped_column(String(256))
    section: Mapped[str | None] = mapped_column(String(256))
    article_label: Mapped[str] = mapped_column(String(32), nullable=False)
    article_number: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(512), nullable=False)
    embedding_model: Mapped[str] = mapped_column(String(128), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    __table_args__ = (
        UniqueConstraint("version_id", "article_number", name="uq_legal_version_article"),
        Index("ix_legal_chunks_chunk_key", "chunk_key"),
        Index("idx_legal_chunk_version_article", "version_id", "article_number"),
    )
