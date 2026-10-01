# -*- coding: utf-8 -*-
"""
LLM 客户端：DeepSeek（OpenAI 兼容接口）懒加载单例 + .env 加载。
base_url / key / model 全部 env 可配，换网关换模型只改 .env。
"""
import os
import time
from pathlib import Path

from openai import OpenAI

from ..infra.observability import LLM_LATENCY, LLM_REQUESTS, LLM_TOKENS
from ..infra.semaphore import leased_semaphore

PROJECT_ROOT = Path(__file__).resolve().parents[3]
_instance = None


def load_dotenv() -> None:
    """读 .env（KEY=VALUE 行）进 os.environ，.env 是最终权威。

    用直接覆盖而非 setdefault：shell 里可能预置了别的 key（如 Claude Code 注入的
    DEEPSEEK_API_KEY），setdefault 会跳过 .env 导致项目用错 key。
    """
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ[key.strip()] = value.strip().strip('"').strip("'")


def get_client() -> OpenAI:
    """懒加载单例：读 env 建 OpenAI 客户端（指向 DeepSeek）。"""
    global _instance
    if _instance is None:
        api_key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "未找到 LLM API key：请把 DEEPSEEK_API_KEY 写入 .env（参考 .env.example）"
            )
        base_url = os.environ.get("OPENAI_BASE_URL", "https://api.deepseek.com")
        # 显式 timeout（默认 60s，LLM_TIMEOUT 可配），避免网络抖动时请求无限挂起
        timeout = float(os.environ.get("LLM_TIMEOUT", "60"))
        _instance = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)
    return _instance


def get_model() -> str:
    """读模型名（env 可配，默认 deepseek-chat）。"""
    return os.environ.get("LLM_MODEL", "deepseek-chat")


def chat(messages: list, **kw):
    """带重试的 LLM 调用：DeepSeek 偶发 5xx/超时，重试 3 次（1s/2s/3s 退避）再抛。

    model 已在内部带上，调用方只需传 messages 与其它参数（tools/response_format 等）。
    3 次都失败则抛出最后一次异常，交给上层处理。
    """
    client, model = get_client(), get_model()
    last = None
    for attempt in range(3):
        started = time.perf_counter()
        try:
            with leased_semaphore(
                f"ai:semaphore:model:{model}",
                int(os.environ.get("MODEL_LLM_CONCURRENCY", "8")),
                lease_seconds=float(os.environ.get("MODEL_SEMAPHORE_LEASE_SECONDS", "90")),
                wait_seconds=float(os.environ.get("MODEL_SEMAPHORE_WAIT_SECONDS", "30")),
                scope="model_llm",
            ):
                response = client.chat.completions.create(model=model, messages=messages, **kw)
            LLM_REQUESTS.labels(model, "success").inc()
            usage = getattr(response, "usage", None)
            if usage:
                LLM_TOKENS.labels(model, "prompt").inc(getattr(usage, "prompt_tokens", 0) or 0)
                LLM_TOKENS.labels(model, "completion").inc(getattr(usage, "completion_tokens", 0) or 0)
            return response
        except Exception as e:  # noqa: BLE001 —— 任意网络/服务异常都该重试
            last = e
            LLM_REQUESTS.labels(model, "error").inc()
            time.sleep(attempt + 1)
        finally:
            LLM_LATENCY.labels(model).observe(time.perf_counter() - started)
    raise last


# 模块导入即加载 .env，各入口不必各自 load
load_dotenv()
