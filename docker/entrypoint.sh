#!/bin/sh
set -eu

if [ "${RUN_MIGRATIONS:-0}" = "1" ]; then
    echo "[bootstrap] 执行数据库迁移..."
    alembic upgrade head
fi

MODEL_DIR="models/bge-small-zh-v1.5"
RERANK_MODEL_DIR="models/bge-reranker-base"

# 语料派生产物和模型均可从版本库中的源码重建；只在首次启动时准备。
if [ ! -f corpus/chunks.json ]; then
    echo "[bootstrap] 生成法条 chunks..."
    python -m backend.app.core.chunking
fi

if [ ! -f "$MODEL_DIR/config.json" ] || { [ ! -f "$MODEL_DIR/model.safetensors" ] && [ ! -f "$MODEL_DIR/pytorch_model.bin" ]; } \
   || { [ "${RERANK:-0}" = "1" ] && { [ ! -f "$RERANK_MODEL_DIR/config.json" ] || { [ ! -f "$RERANK_MODEL_DIR/model.safetensors" ] && [ ! -f "$RERANK_MODEL_DIR/pytorch_model.bin" ]; }; }; }; then
    echo "[bootstrap] 下载 embedding / reranker 模型（首次启动可能需要几分钟）..."
    python -m backend.scripts.download_model
fi

# 提前构建 FAISS 索引，避免首次提问等待向量化；已有索引时跳过。
if [ ! -f corpus/chunks.faiss ]; then
    echo "[bootstrap] 构建 FAISS 索引..."
    python -c "from backend.app.core.retriever import get_retriever; get_retriever()"
fi

exec "$@"
