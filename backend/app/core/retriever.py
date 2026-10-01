# -*- coding: utf-8 -*-
"""
FAISS 向量检索：IndexFlatIP + 归一化向量 => 打分即余弦相似度。
索引落盘复用（向量下标即 chunk 主键），懒加载单例 get_retriever()。
"""
import json
from pathlib import Path

import faiss
import numpy as np
from sqlalchemy import select

from .embeddings import embed_documents, embed_query
from .corpus_store import vector_backend

PROJECT_ROOT = Path(__file__).resolve().parents[3]
CHUNKS_PATH = PROJECT_ROOT / "corpus" / "chunks.json"
INDEX_PATH = PROJECT_ROOT / "corpus" / "chunks.faiss"

_instance = None  # 模块级单例


class Retriever:
    """向量检索器：index 存向量，chunks 存元数据，两者按下标对齐。"""

    def __init__(self, index, chunks: list[dict]) -> None:
        self.index = index
        self.chunks = chunks

    # ---------- 构建 / 加载 ----------
    @classmethod
    def build(cls) -> "Retriever":
        """从 chunks.json 全量建索引，并落盘（只在首次跑一次）。"""
        chunks: list[dict] = json.loads(CHUNKS_PATH.read_text(encoding="utf-8"))
        print(f"正在向量化 {len(chunks)} 个 chunk ...")
        vecs = embed_documents([c["文本"] for c in chunks])

        # bge-small 是 512 维；dim 从向量形状拿，不写死魔法数
        vecs = np.ascontiguousarray(vecs, dtype=np.float32)
        index = faiss.IndexFlatIP(vecs.shape[1])
        index.add(vecs)

        faiss.write_index(index, str(INDEX_PATH))
        print(f"[OK] 索引已落盘：{INDEX_PATH}")
        return cls(index=index, chunks=chunks)

    @classmethod
    def load(cls) -> "Retriever":
        """读落盘索引 + chunks 元数据，秒级启动。"""
        index = faiss.read_index(str(INDEX_PATH))
        chunks: list[dict] = json.loads(CHUNKS_PATH.read_text(encoding="utf-8"))
        return cls(index=index, chunks=chunks)

    # ---------- 检索 ----------
    def _ranked(self, query: str, k: int) -> list[tuple[dict, float]]:
        """内部：返回 top-k 的 (chunk, 向量分数)，供 RRF 按稳定 chunk_key 融合。"""
        q = embed_query(query).reshape(1, -1)
        scores, ids = self.index.search(q, k)
        return [
            (self.chunks[int(i)], float(s))
            for s, i in zip(scores[0], ids[0])
            if i >= 0  # 索引条数不足 k 时，多余槽位是 -1，跳过
        ]

    def search(self, query: str, k: int = 5) -> list:
        """对外：返回 top-k chunk 元数据（带 score），按相似度降序。"""
        return [
            {**chunk, "score": round(s, 4)}
            for chunk, s in self._ranked(query, k)
        ]


class PgVectorRetriever:
    """PostgreSQL 精确余弦检索；第一阶段不创建任何近似向量索引。"""

    def _ranked(self, query: str, k: int) -> list[tuple[dict, float]]:
        from ..infra.database import session_scope
        from ..infra.models import LegalChunk, LegalDocument, LegalDocumentVersion

        query_vector = embed_query(query).astype(np.float32).tolist()
        distance = LegalChunk.embedding.cosine_distance(query_vector).label("distance")
        stmt = (
            select(LegalChunk, LegalDocument.name, distance)
            .join(LegalDocumentVersion, LegalChunk.version_id == LegalDocumentVersion.id)
            .join(LegalDocument, LegalDocumentVersion.document_id == LegalDocument.id)
            .where(
                LegalDocument.jurisdiction == "CN",
                LegalDocumentVersion.status == "active",
            )
            .order_by(distance, LegalChunk.id)
            .limit(k)
        )
        with session_scope() as db:
            rows = db.execute(stmt).all()
        return [
            ({
                "chunk_key": row.LegalChunk.chunk_key,
                "法律": row.name,
                "章": row.LegalChunk.chapter,
                "节": row.LegalChunk.section,
                "条号": row.LegalChunk.article_label,
                "序数": row.LegalChunk.article_number,
                "文本": row.LegalChunk.content,
            }, 1.0 - float(row.distance))
            for row in rows
        ]

    def search(self, query: str, k: int = 5) -> list:
        return [{**chunk, "score": round(score, 4)} for chunk, score in self._ranked(query, k)]


def get_retriever() -> Retriever | PgVectorRetriever:
    """懒加载单例：有落盘索引就 load，没有就 build。"""
    global _instance
    if _instance is None:
        if vector_backend() == "pgvector":
            _instance = PgVectorRetriever()
        else:
            _instance = Retriever.load() if INDEX_PATH.exists() else Retriever.build()
    return _instance
