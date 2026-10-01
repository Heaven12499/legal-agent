# -*- coding: utf-8 -*-
"""Redis ZSET + Lua 容量型租约信号量，用于跨进程治理 AI 并发。"""
import os
import random
import threading
import time
import uuid
from contextlib import contextmanager

from .observability import REDIS_FAILURES, SEMAPHORE_IN_USE, SEMAPHORE_WAIT
from .redis_client import get_redis

ACQUIRE_SCRIPT = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local expires = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local token = ARGV[4]
redis.call('ZREMRANGEBYSCORE', key, '-inf', now)
if redis.call('ZSCORE', key, token) then
  redis.call('ZADD', key, expires, token)
  redis.call('PEXPIRE', key, math.max(expires - now, 1000) * 2)
  return 1
end
if redis.call('ZCARD', key) >= limit then return 0 end
redis.call('ZADD', key, expires, token)
redis.call('PEXPIRE', key, math.max(expires - now, 1000) * 2)
return 1
"""

RENEW_SCRIPT = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local expires = tonumber(ARGV[2])
local token = ARGV[3]
local score = redis.call('ZSCORE', key, token)
if not score then return 0 end
if tonumber(score) <= now then
  redis.call('ZREM', key, token)
  return 0
end
redis.call('ZADD', key, expires, token)
redis.call('PEXPIRE', key, math.max(expires - now, 1000) * 2)
return 1
"""

RELEASE_SCRIPT = """
return redis.call('ZREM', KEYS[1], ARGV[1])
"""


class SemaphoreBusy(RuntimeError):
    pass


class SemaphoreUnavailable(RuntimeError):
    pass


class LeaseSemaphore:
    def __init__(self, key: str, limit: int, lease_seconds: float, *, client=None, scope="runtime"):
        if limit < 1 or lease_seconds <= 0:
            raise ValueError("limit 和 lease_seconds 必须大于 0")
        self.key = key
        self.limit = limit
        self.lease_seconds = lease_seconds
        self.client = client if client is not None else get_redis()
        self.scope = scope
        self._counted_tokens: set[str] = set()
        if self.client is None and os.environ.get("APP_ENV") == "production":
            raise SemaphoreUnavailable("生产环境未配置 Redis 并发控制")

    def try_acquire(self, token: str, *, now_ms: int | None = None) -> bool:
        if self.client is None:
            return True
        now = now_ms if now_ms is not None else int(time.time() * 1000)
        expires = now + int(self.lease_seconds * 1000)
        try:
            return bool(self.client.eval(
                ACQUIRE_SCRIPT, 1, self.key, now, expires, self.limit, token
            ))
        except Exception as exc:  # noqa: BLE001
            REDIS_FAILURES.labels("semaphore_acquire").inc()
            raise SemaphoreUnavailable("Redis 并发控制暂不可用") from exc

    def acquire(self, token: str, wait_seconds: float = 0) -> None:
        started = time.monotonic()
        deadline = started + max(wait_seconds, 0)
        while True:
            if self.try_acquire(token):
                SEMAPHORE_WAIT.labels(self.scope).observe(time.monotonic() - started)
                if token not in self._counted_tokens:
                    self._counted_tokens.add(token)
                    SEMAPHORE_IN_USE.labels(self.scope).inc()
                return
            if time.monotonic() >= deadline:
                SEMAPHORE_WAIT.labels(self.scope).observe(time.monotonic() - started)
                raise SemaphoreBusy(f"{self.scope} 并发额度已满")
            time.sleep(min(0.2 + random.random() * 0.2, max(0, deadline - time.monotonic())))

    def renew(self, token: str, *, now_ms: int | None = None) -> bool:
        if self.client is None:
            return True
        now = now_ms if now_ms is not None else int(time.time() * 1000)
        expires = now + int(self.lease_seconds * 1000)
        try:
            return bool(self.client.eval(RENEW_SCRIPT, 1, self.key, now, expires, token))
        except Exception as exc:  # noqa: BLE001
            REDIS_FAILURES.labels("semaphore_renew").inc()
            raise SemaphoreUnavailable("Redis 租约续期失败") from exc

    def release(self, token: str) -> bool:
        if self.client is None:
            if token in self._counted_tokens:
                self._counted_tokens.remove(token)
                SEMAPHORE_IN_USE.labels(self.scope).dec()
            return True
        try:
            released = bool(self.client.eval(RELEASE_SCRIPT, 1, self.key, token))
            if released and token in self._counted_tokens:
                self._counted_tokens.remove(token)
                SEMAPHORE_IN_USE.labels(self.scope).dec()
            return released
        except Exception as exc:  # noqa: BLE001
            REDIS_FAILURES.labels("semaphore_release").inc()
            raise SemaphoreUnavailable("Redis 租约释放失败") from exc


@contextmanager
def leased_semaphore(
    key: str,
    limit: int,
    *,
    lease_seconds: float = 90,
    wait_seconds: float = 0,
    renew_interval: float | None = None,
    scope: str = "runtime",
):
    """获取后后台续租；本地未配置 Redis 时退化为无操作上下文。"""
    sem = LeaseSemaphore(key, limit, lease_seconds, scope=scope)
    token = uuid.uuid4().hex
    sem.acquire(token, wait_seconds=wait_seconds)
    stop = threading.Event()
    lost = threading.Event()
    interval = renew_interval or max(1.0, lease_seconds / 3)

    def _renew_loop():
        while not stop.wait(interval):
            try:
                if not sem.renew(token):
                    lost.set()
                    return
            except SemaphoreUnavailable:
                lost.set()
                return

    thread = None
    if sem.client is not None:
        thread = threading.Thread(target=_renew_loop, name=f"lease-renew-{scope}", daemon=True)
        thread.start()
    try:
        yield {"token": token, "lost": lost}
    finally:
        stop.set()
        if thread:
            thread.join(timeout=min(interval, 1.0))
        try:
            sem.release(token)
        except SemaphoreUnavailable:
            if os.environ.get("APP_ENV") != "production":
                pass
