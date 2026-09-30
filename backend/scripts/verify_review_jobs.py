# -*- coding: utf-8 -*-
"""任务仓储验收：事务 Outbox、租约领取、幂等完成、隔离、重试与恢复。"""
import os
import tempfile
from pathlib import Path

with tempfile.TemporaryDirectory() as tmp:
    os.environ.pop("DATABASE_URL", None)
    os.environ["SESSION_DB"] = str(Path(tmp) / "review-jobs.db")

    from backend.app.infra import review_jobs, session

    uid, sid = 7, "review-session"
    job = review_jobs.create_with_message(
        uid, sid, "请审查违约金条款",
        {"message": "请审查违约金条款", "history": []}, max_attempts=2,
    )
    job_id = job["job_id"]
    assert job["status"] == "queued" and review_jobs.pending_outbox()
    assert review_jobs.get(999, job_id) is None

    claimed = review_jobs.claim(job_id, worker_id="test-worker")
    assert claimed and claimed["attempts"] == 1
    agent_result = {
        "answer": "审查完成", "trace": [{"round": 1, "tool": "retrieve"}],
        "citation_check": {"total": 0, "valid": [], "invalid": []},
    }
    first = review_jobs.complete(job_id, agent_result)
    second = review_jobs.complete(job_id, agent_result)
    assert first == second, "重复投递应返回同一结果"
    history = session.get_history(uid, sid)
    assert [m["role"] for m in history] == ["user", "assistant"]
    assert history[-1]["trace"][0]["tool"] == "retrieve"

    bad = review_jobs.create_with_message(
        uid, sid, "失败样本", {"message": "失败样本", "history": []}, max_attempts=1
    )
    review_jobs.claim(bad["job_id"], worker_id="test-worker", lease_seconds=-1)
    assert not review_jobs.fail(bad["job_id"], "TimeoutError: 模拟上游超时")
    assert review_jobs.get(uid, bad["job_id"])["status"] == "failed"
    assert review_jobs.retry(uid, bad["job_id"])["status"] == "queued"

    crashed = review_jobs.create_with_message(
        uid, sid, "崩溃样本", {"message": "崩溃样本", "history": []}, max_attempts=2
    )
    review_jobs.claim(crashed["job_id"], worker_id="dead-worker", lease_seconds=-1)
    recovered = review_jobs.recover_incomplete()
    assert crashed["job_id"] in recovered
    assert review_jobs.get(uid, crashed["job_id"])["status"] == "queued"

    from backend.app.infra.database import get_engine
    get_engine().dispose()

print("[OK] 审查任务：事务 Outbox、租约、幂等完成、隔离、重试与恢复")
