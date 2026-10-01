# -*- coding: utf-8 -*-
"""法律语料统一读取层：迁移期支持 JSON/FAISS 与 PostgreSQL/pgvector 双后端。"""
import json
import os
from datetime import date
from pathlib import Path

from sqlalchemy import func, or_, select

PROJECT_ROOT = Path(__file__).resolve().parents[3]
CHUNKS_PATH = PROJECT_ROOT / "corpus" / "chunks.json"
EXPECTED_CHUNKS = 1023


def vector_backend() -> str:
    backend = os.environ.get("VECTOR_BACKEND", "faiss").strip().lower()
    if backend not in {"faiss", "pgvector"}:
        raise ValueError(f"不支持的 VECTOR_BACKEND={backend!r}，只能是 faiss 或 pgvector")
    return backend


def chunk_key(chunk: dict) -> str:
    return f"{chunk['法律']}:{int(chunk['序数'])}"


def _json_chunks() -> list[dict]:
    return json.loads(CHUNKS_PATH.read_text(encoding="utf-8"))


def _pg_chunks(*, contract_date: date | None = None, jurisdiction: str = "CN") -> list[dict]:
    from ..infra.database import session_scope
    from ..infra.models import LegalChunk, LegalDocument, LegalDocumentVersion

    stmt = (
        select(
            LegalChunk.chunk_key, LegalChunk.chapter, LegalChunk.section,
            LegalChunk.article_label, LegalChunk.article_number, LegalChunk.content,
            LegalDocument.name,
        )
        .join(LegalDocumentVersion, LegalChunk.version_id == LegalDocumentVersion.id)
        .join(LegalDocument, LegalDocumentVersion.document_id == LegalDocument.id)
        .where(
            LegalDocument.jurisdiction == jurisdiction,
            LegalDocumentVersion.status == "active",
        )
        .order_by(LegalDocument.name, LegalChunk.article_number)
    )
    if contract_date is not None:
        stmt = stmt.where(
            or_(LegalDocumentVersion.effective_from.is_(None),
                LegalDocumentVersion.effective_from <= contract_date),
            or_(LegalDocumentVersion.effective_to.is_(None),
                LegalDocumentVersion.effective_to > contract_date),
        )
    with session_scope() as db:
        rows = db.execute(stmt).all()
    return [
        {
            "chunk_key": row.chunk_key,
            "法律": row.name,
            "章": row.chapter,
            "节": row.section,
            "条号": row.article_label,
            "序数": row.article_number,
            "文本": row.content,
        }
        for row in rows
    ]


def load_chunks(*, contract_date: date | None = None, jurisdiction: str = "CN") -> list[dict]:
    if vector_backend() == "pgvector":
        return _pg_chunks(contract_date=contract_date, jurisdiction=jurisdiction)
    return _json_chunks()


def corpus_ready() -> bool:
    try:
        if vector_backend() == "faiss":
            return CHUNKS_PATH.exists() and len(_json_chunks()) == EXPECTED_CHUNKS
        from ..infra.database import session_scope
        from ..infra.models import LegalChunk, LegalDocument, LegalDocumentVersion

        stmt = (
            select(func.count(LegalChunk.id))
            .join(LegalDocumentVersion, LegalChunk.version_id == LegalDocumentVersion.id)
            .join(LegalDocument, LegalDocumentVersion.document_id == LegalDocument.id)
            .where(
                LegalDocument.jurisdiction == "CN",
                LegalDocumentVersion.status == "active",
            )
        )
        with session_scope() as db:
            return db.scalar(stmt) == EXPECTED_CHUNKS
    except Exception:
        return False
