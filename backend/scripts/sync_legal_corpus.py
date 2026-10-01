# -*- coding: utf-8 -*-
"""把版本库中的 1,023 条法律语料幂等同步到 PostgreSQL + pgvector。

优先重用现有 FAISS IndexFlatIP 中的归一化向量，保证迁移对账时向量完全一致；
索引不存在或数量不匹配时才调用本地 BGE 模型重新生成。
"""
import argparse
import hashlib
import json
import os
import re
from datetime import date
from pathlib import Path

import numpy as np
from sqlalchemy import select

from backend.app.core.corpus_store import CHUNKS_PATH, EXPECTED_CHUNKS, chunk_key
from backend.app.core.embeddings import embed_documents
from backend.app.infra.database import database_url, session_scope
from backend.app.infra.distributed_lock import distributed_lock
from backend.app.infra.models import LegalChunk, LegalDocument, LegalDocumentVersion

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FAISS_PATH = PROJECT_ROOT / "corpus" / "chunks.faiss"
EMBEDDING_MODEL = "BAAI/bge-small-zh-v1.5"

DOCUMENT_META = {
    "劳动合同法": ("law", "2012 amendment", date(2013, 7, 1)),
    "劳动法": ("law", "2018 amendment", date(2018, 12, 29)),
    "劳动争议调解仲裁法": ("law", "2007 promulgation", date(2008, 5, 1)),
    "劳动合同法实施条例": ("administrative_regulation", "2008 promulgation", date(2008, 9, 18)),
    "社会保险法": ("law", "2018 amendment", date(2018, 12, 29)),
    "民法典（合同编）": ("code_book", "2020 promulgation", date(2021, 1, 1)),
    "合同编通则解释": ("judicial_interpretation", "法释〔2023〕13号", date(2023, 12, 5)),
    "买卖合同解释": ("judicial_interpretation", "法释〔2020〕17号", date(2021, 1, 1)),
}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _source_urls() -> dict[str, str | None]:
    from backend.scripts.preprocess_corpus import LAW_CONFIGS

    urls = {}
    for cfg in LAW_CONFIGS:
        match = re.search(r"https?://[^\s）]+", cfg.get("source", ""))
        urls[cfg["name"]] = match.group(0) if match else None
    return urls


def _vectors(chunks: list[dict]) -> tuple[np.ndarray, str]:
    if FAISS_PATH.exists():
        import faiss

        index = faiss.read_index(str(FAISS_PATH))
        if index.ntotal == len(chunks) and index.d == 512:
            values = np.empty((index.ntotal, index.d), dtype=np.float32)
            index.reconstruct_n(0, index.ntotal, values)
            return values, "faiss_reconstruct"
    return np.asarray(embed_documents([chunk["文本"] for chunk in chunks]), dtype=np.float32), "bge_encode"


def sync(*, check_only: bool = False) -> dict:
    if not database_url().startswith("postgresql"):
        raise RuntimeError("pgvector 语料同步只支持 PostgreSQL，请配置 DATABASE_URL 或 DB_HOST")
    chunks = json.loads(CHUNKS_PATH.read_text(encoding="utf-8"))
    if len(chunks) != EXPECTED_CHUNKS:
        raise RuntimeError(f"语料数量异常：期望 {EXPECTED_CHUNKS}，实际 {len(chunks)}")

    if check_only:
        with session_scope() as db:
            count = len(db.scalars(select(LegalChunk.id)).all())
        if count != EXPECTED_CHUNKS:
            raise RuntimeError(f"数据库语料数量异常：期望 {EXPECTED_CHUNKS}，实际 {count}")
        return {"total": count, "mode": "check"}

    vectors, vector_source = _vectors(chunks)
    sources = _source_urls()
    grouped: dict[str, list[tuple[int, dict]]] = {}
    for index, chunk in enumerate(chunks):
        grouped.setdefault(chunk["法律"], []).append((index, chunk))

    inserted = updated = unchanged = 0
    with session_scope() as db:
        existing_chunks = {
            (row.version_id, row.chunk_key): row
            for row in db.scalars(select(LegalChunk)).all()
        }
        for law_name, entries in grouped.items():
            document = db.scalar(select(LegalDocument).where(LegalDocument.name == law_name))
            doc_type, version_label, effective_from = DOCUMENT_META[law_name]
            if document is None:
                document = LegalDocument(
                    name=law_name, jurisdiction="CN", document_type=doc_type,
                    source_url=sources.get(law_name),
                )
                db.add(document)
                db.flush()
            else:
                document.jurisdiction = "CN"
                document.document_type = doc_type
                document.source_url = sources.get(law_name)
            version_hash = _sha("\n".join(chunk["文本"] for _, chunk in entries))
            version = db.scalar(select(LegalDocumentVersion).where(
                LegalDocumentVersion.document_id == document.id,
                LegalDocumentVersion.version_label == version_label,
            ))
            if version is None:
                version = LegalDocumentVersion(
                    document_id=document.id, version_label=version_label, status="active",
                    effective_from=effective_from, effective_to=None, content_hash=version_hash,
                )
                db.add(version)
                db.flush()
            else:
                version.status = "active"
                version.effective_from = effective_from
                version.effective_to = None
                version.content_hash = version_hash

            for index, source in entries:
                key = chunk_key(source)
                digest = _sha(source["文本"])
                row = existing_chunks.get((version.id, key))
                metadata = {
                    "version_id": version.id,
                    "chapter": source.get("章"), "section": source.get("节"),
                    "article_label": source["条号"], "article_number": source["序数"],
                }
                if row is None:
                    db.add(LegalChunk(
                        chunk_key=key, **metadata, content=source["文本"],
                        embedding=vectors[index].tolist(), embedding_model=EMBEDDING_MODEL,
                        content_hash=digest,
                    ))
                    inserted += 1
                else:
                    metadata_changed = any(getattr(row, field) != value for field, value in metadata.items())
                    content_changed = row.content_hash != digest or row.embedding_model != EMBEDDING_MODEL
                    for field, value in metadata.items():
                        setattr(row, field, value)
                    if content_changed:
                        row.content = source["文本"]
                        row.embedding = vectors[index].tolist()
                        row.embedding_model = EMBEDDING_MODEL
                        row.content_hash = digest
                    if metadata_changed or content_changed:
                        updated += 1
                    else:
                        unchanged += 1

    return {
        "total": len(chunks), "inserted": inserted, "updated": updated,
        "unchanged": unchanged, "vector_source": vector_source,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="只检查数据库语料数量")
    args = parser.parse_args()
    if args.check:
        print(json.dumps(sync(check_only=True), ensure_ascii=False))
        return
    corpus_version = os.environ.get("LEGAL_CORPUS_VERSION", "v1")
    with distributed_lock(
        f"lock:legal-corpus:sync:{corpus_version}",
        lease_seconds=int(os.environ.get("CORPUS_SYNC_LOCK_SECONDS", "1800")),
        wait_seconds=int(os.environ.get("CORPUS_SYNC_LOCK_WAIT_SECONDS", "600")),
    ):
        print(json.dumps(sync(), ensure_ascii=False))


if __name__ == "__main__":
    main()
