# -*- coding: utf-8 -*-
"""Redis 固定窗口限流；无 REDIS_URL 的本地测试使用进程内回退。"""
import os
import threading
import time

from fastapi import HTTPException

_client = None
_local: dict[str, tuple[int, float]] = {}
_lock = threading.Lock()


def get_redis():
    global _client
    url = os.environ.get("REDIS_URL")
    if not url:
        return None
    if _client is None:
        import redis
        _client = redis.Redis.from_url(url, decode_responses=True, socket_timeout=1)
    return _client


def ping() -> bool:
    client = get_redis()
    return bool(client and client.ping())


def enforce(bucket: str, identity: str, limit: int, window_seconds: int) -> None:
    slot = int(time.time() // window_seconds)
    key = f"legal-rag:limit:{bucket}:{identity}:{slot}"
    client = get_redis()
    try:
        if client:
            pipe = client.pipeline()
            pipe.incr(key)
            pipe.expire(key, window_seconds + 2)
            count, _ = pipe.execute()
        else:
            now = time.time()
            with _lock:
                count, expires = _local.get(key, (0, now + window_seconds))
                count += 1
                _local[key] = (count, expires)
                if len(_local) > 10_000:
                    for old_key, (_, old_expiry) in list(_local.items()):
                        if old_expiry < now:
                            _local.pop(old_key, None)
    except Exception as exc:  # Redis 故障时昂贵审查接口应保护性失败
        if os.environ.get("APP_ENV") == "production":
            raise HTTPException(status_code=503, detail="限流服务暂不可用") from exc
        count = 1
    if count > limit:
        raise HTTPException(
            status_code=429,
            detail=f"请求过于频繁，请在 {window_seconds} 秒后重试",
            headers={"Retry-After": str(window_seconds)},
        )
