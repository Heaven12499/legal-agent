# -*- coding: utf-8 -*-
"""
向量 + BM25 双路检索，RRF（Reciprocal Rank Fusion）融合取 top-k。
RRF 只看排名不看分数，规避两路分数量纲不可比的问题（原理见 README 设计决策）。
"""
import logging

from .bm25 import get_bm25
from .corpus_store import chunk_key
from .retriever import get_retriever
from .rerank import enabled

_instance = None
_log = logging.getLogger(__name__)
_rerank_fallback_warned = False


class HybridRetriever:
    """双路融合检索器：内部持有向量检索器和 BM25 检索器。"""

    def __init__(self, vector, bm25) -> None:
        self.vector = vector
        self.bm25 = bm25

    def search(self, query: str, k: int = 5, rrf_k: int = 60, n: int = 20) -> list:
        """双路各取 n 个候选，RRF 融合后返回 top-k chunk（带各路排位）。

        参数说明：
            k     最终返回条数
            rrf_k RRF 的平滑常数（越大，融合分越"平均"，排名差距影响越小）
            n     每路候选数，要比 k 大——融合才有得选，不是拿两路 top-k 硬拼
        """
        # 双路各自的"排名表"：稳定 chunk_key -> 该路中的排位（0 起）
        vector_rows = self.vector._ranked(query, n)
        bm25_rows = self.bm25._ranked(query, n)
        chunks = {chunk_key(chunk): chunk for chunk, _ in [*vector_rows, *bm25_rows]}
        vec_rank = {chunk_key(chunk): r for r, (chunk, _) in enumerate(vector_rows)}
        bm25_rank = {chunk_key(chunk): r for r, (chunk, _) in enumerate(bm25_rows)}

        # RRF 融合：只有某一路命中的 chunk，融合分也只有一个加项
        fused: dict[str, float] = {}
        for i, r in vec_rank.items():
            fused[i] = 1.0 / (rrf_k + r)
        for i, r in bm25_rank.items():
            fused[i] = fused.get(i, 0.0) + 1.0 / (rrf_k + r)

        # 按融合分降序先取 top-n 候选（比 k 大，供精排/回退有得选）
        ranked = sorted(fused, key=fused.get, reverse=True)
        # 可选精排（RERANK=1）：对 top-n 用 bge-reranker 打分取 top-k；模型不可用则明确告警后回退 RRF。
        if enabled():
            try:
                from .rerank import rerank

                top_n = [chunks[key] for key in ranked[:n]]
                return rerank(query, top_n, k)
            except Exception as exc:  # noqa: BLE001 —— 模型缺失/加载失败，保服务可用性
                global _rerank_fallback_warned
                if not _rerank_fallback_warned:
                    _log.warning("reranker 不可用，已回退至 RRF：%s", exc)
                    _rerank_fallback_warned = True
                pass
        return [
            {
                **chunks[key],
                "score": round(fused[key], 4),
                "向量排位": vec_rank.get(key, None) + 1 if key in vec_rank else None,
                "BM25排位": bm25_rank.get(key, None) + 1 if key in bm25_rank else None,
            }
            for key in ranked[:k]
        ]


def get_hybrid() -> HybridRetriever:
    """懒加载单例：向量（读盘索引）和 BM25 各建一次，之后复用。"""
    global _instance
    if _instance is None:
        _instance = HybridRetriever(get_retriever(), get_bm25())
    return _instance
