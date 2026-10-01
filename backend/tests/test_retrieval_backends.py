import json
from pathlib import Path

import numpy as np

from backend.app.core.corpus_store import EXPECTED_CHUNKS, chunk_key, vector_backend
from backend.app.core.hybrid import HybridRetriever


ROOT = Path(__file__).resolve().parents[2]


def test_corpus_has_stable_unique_chunk_keys(monkeypatch):
    monkeypatch.setenv("VECTOR_BACKEND", "faiss")
    chunks = json.loads((ROOT / "corpus" / "chunks.json").read_text(encoding="utf-8"))
    keys = [chunk_key(chunk) for chunk in chunks]
    assert len(keys) == len(set(keys)) == EXPECTED_CHUNKS
    assert vector_backend() == "faiss"


def test_hybrid_fuses_by_stable_key_not_array_position(monkeypatch):
    monkeypatch.setattr("backend.app.core.hybrid.enabled", lambda: False)
    a = {"法律": "甲法", "序数": 1, "条号": "第一条", "文本": "甲"}
    b = {"法律": "乙法", "序数": 2, "条号": "第二条", "文本": "乙"}

    class FakeRetriever:
        def __init__(self, rows):
            self.rows = rows

        def _ranked(self, query, k):
            return self.rows[:k]

    result = HybridRetriever(
        FakeRetriever([(a, 0.9), (b, 0.8)]),
        FakeRetriever([(b, 3.0), (a, 2.0)]),
    ).search("test", k=2, n=2)

    assert {chunk_key(row) for row in result} == {"甲法:1", "乙法:2"}
    assert all(row["向量排位"] and row["BM25排位"] for row in result)
