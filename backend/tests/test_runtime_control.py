import json

import pytest

from backend.app.infra import review_events
from backend.app.infra.distributed_lock import RELEASE_LOCK_SCRIPT, distributed_lock
from backend.app.infra.semaphore import (
    ACQUIRE_SCRIPT,
    RELEASE_SCRIPT,
    RENEW_SCRIPT,
    LeaseSemaphore,
)


class FakeRedis:
    def __init__(self):
        self.zsets = {}
        self.values = {}
        self.streams = {}
        self.sequence = 0
        self.expirations = {}

    def eval(self, script, _numkeys, key, *args):
        if script == ACQUIRE_SCRIPT:
            now, expires, limit, token = int(args[0]), int(args[1]), int(args[2]), args[3]
            bucket = self.zsets.setdefault(key, {})
            bucket = {name: score for name, score in bucket.items() if score > now}
            self.zsets[key] = bucket
            if token in bucket:
                bucket[token] = expires
                return 1
            if len(bucket) >= limit:
                return 0
            bucket[token] = expires
            return 1
        if script == RENEW_SCRIPT:
            now, expires, token = int(args[0]), int(args[1]), args[2]
            bucket = self.zsets.setdefault(key, {})
            if token not in bucket or bucket[token] <= now:
                bucket.pop(token, None)
                return 0
            bucket[token] = expires
            return 1
        if script == RELEASE_SCRIPT:
            return int(self.zsets.setdefault(key, {}).pop(args[0], None) is not None)
        if script == RELEASE_LOCK_SCRIPT:
            token = args[0]
            if self.values.get(key) == token:
                del self.values[key]
                return 1
            return 0
        raise AssertionError("unknown script")

    def set(self, key, value, *, nx=False, px=None):
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    def xadd(self, key, fields, *, maxlen, approximate):
        self.sequence += 1
        event_id = f"{self.sequence}-0"
        rows = self.streams.setdefault(key, [])
        rows.append((event_id, fields))
        del rows[:-maxlen]
        return event_id

    def xread(self, streams, *, count, block):
        key, last_id = next(iter(streams.items()))
        last_seq = int(last_id.split("-", 1)[0])
        rows = [row for row in self.streams.get(key, []) if int(row[0].split("-", 1)[0]) > last_seq]
        return [(key, rows[:count])] if rows else []

    def expire(self, key, seconds):
        self.expirations[key] = seconds
        return True


def test_semaphore_capacity_renew_release_and_expiry():
    client = FakeRedis()
    sem = LeaseSemaphore("sem", 2, 1, client=client, scope="test")
    assert sem.try_acquire("a", now_ms=1000)
    assert sem.try_acquire("b", now_ms=1000)
    assert not sem.try_acquire("c", now_ms=1000)
    assert sem.renew("a", now_ms=1500)
    assert not sem.release("someone-else")
    assert sem.release("b")
    assert sem.try_acquire("c", now_ms=1600)
    # a/c 的租约到期后，无需崩溃进程主动释放即可回收容量。
    assert sem.try_acquire("d", now_ms=3001)


def test_distributed_lock_only_owner_can_release(monkeypatch):
    client = FakeRedis()
    monkeypatch.setattr("backend.app.infra.distributed_lock.get_redis", lambda: client)
    with distributed_lock("lock:test", lease_seconds=10) as token:
        assert client.values["lock:test"] == token
        assert client.eval(RELEASE_LOCK_SCRIPT, 1, "lock:test", "wrong-token") == 0
        assert "lock:test" in client.values
    assert "lock:test" not in client.values


def test_stream_publish_resume_and_sse_encoding(monkeypatch):
    client = FakeRedis()
    monkeypatch.setattr(review_events, "get_redis", lambda: client)
    monkeypatch.setattr(review_events, "get_stream_redis", lambda: client)
    first = review_events.publish("job-1", "started", progress=10)
    second = review_events.publish(
        "job-1", "agent_trace", progress=60,
        trace={"tool": "retrieve", "hits": ["民法典第五百八十五条"]},
    )
    assert first == "1-0" and second == "2-0"
    assert client.expirations[review_events.stream_key("job-1")] == 86400
    resumed = review_events.read("job-1", first, block_ms=0)
    assert [event_id for event_id, _ in resumed] == [second]
    assert resumed[0][1]["trace"]["tool"] == "retrieve"
    frame = review_events.encode_sse(second, resumed[0][1])
    assert "id: 2-0" in frame and "event: agent_trace" in frame
    payload = json.loads(next(line[6:] for line in frame.splitlines() if line.startswith("data: ")))
    assert payload["progress"] == 60
