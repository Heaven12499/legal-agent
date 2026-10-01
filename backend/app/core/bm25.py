# -*- coding: utf-8 -*-
"""
BM25 词法检索器：词面精确匹配，和向量检索互补。
jieba 分词后喂 rank-bm25——中文不分词，"经济补偿"会散成单字匹配不上。
"""
import jieba
import numpy as np
from rank_bm25 import BM25Okapi

from .corpus_store import load_chunks

_instance = None


class BM25Retriever:
    """词法检索器：index 存分词后的语料，chunks 存元数据，按下标对齐。"""

    def __init__(self, bm25, chunks: list[dict]) -> None:
        self.bm25 = bm25
        self.chunks = chunks

    @classmethod
    def build(cls) -> "BM25Retriever":
        """从 chunks.json 建 BM25 索引（jieba 分词后喂给 rank-bm25）。

        1,023 条全量打分 O(N) 毫秒级，不需要落盘，每次进程内重建即可。
        """
        chunks: list[dict] = load_chunks()
        corpus_tokens = [jieba.lcut(c["文本"]) for c in chunks]
        return cls(BM25Okapi(corpus_tokens), chunks)

    def _ranked(self, query: str, k: int) -> list[tuple[dict, float]]:
        """内部：返回 top-k 的 (chunk, BM25分数)，供 RRF 融合用。

        分数为 0 的文档（查询词一个都没撞上）直接丢弃——词面没交集，
        靠 BM25 兜底的意义就是精确命中，不该混进噪声。
        """
        tokens = jieba.lcut(query)
        if not tokens:
            return []
        scores = self.bm25.get_scores(tokens)
        order = np.argsort(scores)[::-1]
        return [
            (self.chunks[int(i)], float(scores[i]))
            for i in order[:k]
            if scores[i] > 0
        ]

    def search(self, query: str, k: int = 5) -> list:
        """对外：返回 top-k chunk（带 BM25 分数），单独用或做消融对比。"""
        return [
            {**chunk, "score": round(s, 4)}
            for chunk, s in self._ranked(query, k)
        ]


def get_bm25() -> BM25Retriever:
    """懒加载单例：建索引很快，进程内只建一次。"""
    global _instance
    if _instance is None:
        _instance = BM25Retriever.build()
    return _instance
