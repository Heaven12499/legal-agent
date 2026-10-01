# -*- coding: utf-8 -*-
"""带唯一令牌和租约的 Redis 互斥锁，仅用于防止可重建任务重复执行。"""
import time
import uuid
import os
from contextlib import contextmanager

from .observability import REDIS_FAILURES
from .redis_client import get_redis

RELEASE_LOCK_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""


class LockBusy(RuntimeError):
    pass


@contextmanager
def distributed_lock(key: str, *, lease_seconds: int = 600, wait_seconds: int = 0):
    client = get_redis()
    if client is None:
        if os.environ.get("APP_ENV") == "production":
            raise RuntimeError("生产环境未配置 Redis 分布式锁")
        yield None
        return
    token = uuid.uuid4().hex
    deadline = time.monotonic() + max(wait_seconds, 0)
    while True:
        try:
            acquired = client.set(key, token, nx=True, px=lease_seconds * 1000)
        except Exception as exc:  # noqa: BLE001
            REDIS_FAILURES.labels("lock_acquire").inc()
            raise RuntimeError("Redis 分布式锁不可用") from exc
        if acquired:
            break
        if time.monotonic() >= deadline:
            raise LockBusy(f"锁正在被其他实例持有：{key}")
        time.sleep(0.25)
    try:
        yield token
    finally:
        try:
            client.eval(RELEASE_LOCK_SCRIPT, 1, key, token)
        except Exception:  # noqa: BLE001 - 租约会自动释放，记录后不覆盖业务异常
            REDIS_FAILURES.labels("lock_release").inc()
