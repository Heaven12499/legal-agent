# -*- coding: utf-8 -*-
"""Redis Streams 审查事件；PostgreSQL 仍是任务状态的权威数据源。"""
import json
import os
import re
import time
from collections.abc import Iterator

from .observability import REDIS_FAILURES, REVIEW_EVENTS, SSE_CONNECTIONS
from .redis_client import get_redis, get_stream_redis

TERMINAL_EVENTS = {"completed", "failed"}
EVENT_MESSAGES = {
    "queued": "审查任务已进入队列",
    "started": "Worker 已开始处理",
    "reviewing": "正在检索证据并审查合同",
    "agent_trace": "Agent 已完成一轮工具调用",
    "persisting": "正在保存审查结果",
    "retrying": "本次执行失败，正在等待重试",
    "completed": "风险报告生成完成",
    "failed": "审查任务执行失败",
}


def stream_key(job_id: str) -> str:
    return f"review:events:{job_id}"


def publish(
    job_id: str,
    event: str,
    *,
    progress: int,
    phase: str | None = None,
    message: str | None = None,
    trace: dict | None = None,
) -> str | None:
    client = get_redis()
    if client is None:
        return None
    payload = {
        "job_id": job_id,
        "event": event,
        "phase": phase or event,
        "progress": max(0, min(int(progress), 100)),
        "message": message or EVENT_MESSAGES.get(event, event),
        "timestamp": time.time(),
    }
    if trace is not None:
        payload["trace"] = trace
    try:
        event_id = client.xadd(
            stream_key(job_id),
            {"data": json.dumps(payload, ensure_ascii=False, separators=(",", ":"))},
            maxlen=int(os.environ.get("REVIEW_EVENT_MAXLEN", "500")),
            approximate=True,
        )
        client.expire(stream_key(job_id), int(os.environ.get("REVIEW_EVENT_TTL_SECONDS", "86400")))
        REVIEW_EVENTS.labels(event).inc()
        return event_id
    except Exception:  # noqa: BLE001 - 事件通道失败不能改变权威任务状态
        REDIS_FAILURES.labels("stream_publish").inc()
        return None


def read(job_id: str, last_id: str = "0-0", *, block_ms: int = 15000) -> list[tuple[str, dict]]:
    client = get_stream_redis()
    if client is None:
        return []
    try:
        batches = client.xread({stream_key(job_id): last_id}, count=50, block=block_ms)
    except Exception as exc:  # noqa: BLE001
        REDIS_FAILURES.labels("stream_read").inc()
        raise RuntimeError("Redis Streams 暂不可用") from exc
    events = []
    for _, rows in batches:
        for event_id, fields in rows:
            try:
                events.append((event_id, json.loads(fields["data"])))
            except (KeyError, TypeError, json.JSONDecodeError):
                REDIS_FAILURES.labels("stream_decode").inc()
    return events


def encode_sse(event_id: str, payload: dict) -> str:
    event = payload.get("event", "progress")
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"id: {event_id}\nevent: {event}\ndata: {data}\n\n"


def _database_snapshot(user_id: int, job_id: str) -> tuple[str, dict] | None:
    from . import review_jobs

    job = review_jobs.get(user_id, job_id)
    if job is None:
        return None
    updated_ms = max(1, int(job["updated_at"] * 1000))
    status = job["status"]
    event = "completed" if status == "succeeded" else status
    payload = {
        "job_id": job_id,
        "event": event,
        "phase": job["phase"],
        "progress": job["progress"],
        "message": EVENT_MESSAGES.get(event, f"任务状态：{status}"),
        "timestamp": job["updated_at"],
        "source": "postgresql_fallback",
    }
    return f"{updated_ms}-0", payload


def stream(user_id: int, job_id: str, last_event_id: str | None = None) -> Iterator[str]:
    """优先阻塞读取 Stream；Redis 故障时降级为低频 PostgreSQL 状态轮询。"""
    cursor = last_event_id if last_event_id and re.fullmatch(r"\d+-\d+", last_event_id) else "0-0"
    last_db_id = None
    SSE_CONNECTIONS.inc()
    try:
        while True:
            if get_stream_redis() is None:
                snapshot = _database_snapshot(user_id, job_id)
                if snapshot and snapshot[0] != last_db_id:
                    last_db_id, payload = snapshot
                    cursor = last_db_id
                    yield encode_sse(last_db_id, payload)
                    if payload["event"] in TERMINAL_EVENTS:
                        return
                time.sleep(1)
                continue
            try:
                events = read(job_id, cursor, block_ms=15000)
            except RuntimeError:
                events = []
                snapshot = _database_snapshot(user_id, job_id)
                if snapshot and snapshot[0] != last_db_id:
                    last_db_id, payload = snapshot
                    cursor = last_db_id
                    yield encode_sse(last_db_id, payload)
                    if payload["event"] in TERMINAL_EVENTS:
                        return
                time.sleep(1)
                continue
            if not events:
                snapshot = _database_snapshot(user_id, job_id)
                if snapshot and snapshot[1]["event"] in TERMINAL_EVENTS:
                    yield encode_sse(*snapshot)
                    return
                yield ": keepalive\n\n"
                continue
            for event_id, payload in events:
                cursor = event_id
                yield encode_sse(event_id, payload)
                if payload.get("event") in TERMINAL_EVENTS:
                    return
    finally:
        SSE_CONNECTIONS.dec()
