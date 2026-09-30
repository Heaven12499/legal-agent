from backend.scripts import download_model


def test_reranker_is_not_downloaded_when_disabled(monkeypatch, tmp_path):
    downloaded = []
    monkeypatch.setattr(
        download_model,
        "EMBEDDING_MODEL",
        ("embedding", tmp_path / "embedding"),
    )
    monkeypatch.setattr(
        download_model,
        "RERANK_MODEL",
        ("reranker", tmp_path / "reranker"),
    )
    monkeypatch.setattr(
        download_model,
        "snapshot_download",
        lambda name, local_dir: downloaded.append((name, local_dir)),
    )
    monkeypatch.delenv("RERANK", raising=False)

    download_model.main()

    assert downloaded == [("embedding", str(tmp_path / "embedding"))]


def test_reranker_is_downloaded_when_enabled(monkeypatch, tmp_path):
    downloaded = []
    monkeypatch.setattr(
        download_model,
        "EMBEDDING_MODEL",
        ("embedding", tmp_path / "embedding"),
    )
    monkeypatch.setattr(
        download_model,
        "RERANK_MODEL",
        ("reranker", tmp_path / "reranker"),
    )
    monkeypatch.setattr(
        download_model,
        "snapshot_download",
        lambda name, local_dir: downloaded.append((name, local_dir)),
    )
    monkeypatch.setenv("RERANK", "1")

    download_model.main()

    assert downloaded == [
        ("embedding", str(tmp_path / "embedding")),
        ("reranker", str(tmp_path / "reranker")),
    ]
