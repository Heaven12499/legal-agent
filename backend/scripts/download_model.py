# -*- coding: utf-8 -*-
"""
下载 embedding 模型，并在 RERANK=1 时下载可选 reranker 模型。

单独一个脚本：模型二进制文件不入库。运行时只读本地 models/，
零网络依赖 —— "运行时可复现"（模型版本和语料一样被钉死）。

用法：
    python -m backend.scripts.download_model

默认走国内 hf-mirror 镜像（直连 huggingface.co 不通）；要换官方源：
    HF_ENDPOINT=https://huggingface.co python -m backend.scripts.download_model
"""
import os
from pathlib import Path

from huggingface_hub import snapshot_download

ROOT = Path(__file__).resolve().parents[2]
EMBEDDING_MODEL = (
    "BAAI/bge-small-zh-v1.5",
    ROOT / "models" / "bge-small-zh-v1.5",
)
RERANK_MODEL = (
    "BAAI/bge-reranker-base",
    ROOT / "models" / "bge-reranker-base",
)


def model_ready(target: Path) -> bool:
    """模型目录必须有配置和权重，不能只因下载中断留下空目录就误判完成。"""
    return (target / "config.json").exists() and any(
        (target / name).exists()
        for name in ("model.safetensors", "pytorch_model.bin")
    )


def main() -> None:
    # 默认走镜像；用户显式设过 HF_ENDPOINT 就尊重用户的
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

    models = [EMBEDDING_MODEL]
    if os.environ.get("RERANK") == "1":
        models.append(RERANK_MODEL)
    else:
        print("RERANK 未启用，跳过可选 reranker 模型")

    for model_name, target in models:
        if model_ready(target):
            print(f"模型已存在：{target}，跳过")
            continue
        print(f"开始下载 {model_name} ...")
        snapshot_download(model_name, local_dir=str(target))
        print(f"[OK] 模型已下载到 {target}")


if __name__ == "__main__":
    main()
