# -*- coding: utf-8 -*-
"""可靠审查任务仓储：事务创建、租约领取、幂等完成与 Outbox。"""
import time
import uuid

from sqlalchemy import or_, select

from .database import session_scope
from .models import ChatSession, Message, ReviewJob, TaskOutbox


def _public(job: ReviewJob, include_payload: bool = False) -> dict:
    fields = (
        "job_id", "user_id", "session_id", "user_message_id", "status", "progress",
        "phase", "attempts", "max_attempts", "result", "error", "worker_id",
        "created_at", "updated_at", "started_at", "finished_at",
    )
    out = {name: getattr(job, name) for name in fields}
    if include_payload:
        out["payload"] = job.payload
    elif out.get("error"):
        # 详细异常保留在数据库与结构化日志，C 端只返回稳定错误文案。
        out["error"] = "审查任务执行失败，请稍后重试"
    return out


def _new_outbox(db, job_id: str, attempt: int) -> None:
    exists = db.scalar(select(TaskOutbox.id).where(
        TaskOutbox.job_id == job_id, TaskOutbox.attempt == attempt
    ))
    if not exists:
        db.add(TaskOutbox(
            job_id=job_id, attempt=attempt, published=False, created_at=time.time()
        ))


def create_with_message(user_id: int, session_id: str, message: str, payload: dict,
                        max_attempts: int = 2, contract_set: bool = False,
                        contract: str | None = None,
                        contract_name: str | None = None) -> dict:
    """在一个数据库事务中更新合同、写用户消息、创建任务和 Outbox。"""
    now = time.time()
    job_id = uuid.uuid4().hex
    with session_scope() as db:
        chat = db.get(ChatSession, session_id)
        if chat is not None and chat.user_id not in (None, user_id):
            raise ValueError("无权操作该会话")
        if chat is None:
            chat = ChatSession(
                session_id=session_id, user_id=user_id, created_at=now, updated_at=now
            )
            db.add(chat)
            db.flush()
        if contract_set:
            chat.contract, chat.contract_name = contract, contract_name
        chat.updated_at = now
        user_message = Message(session_id=session_id, role="user", content=message)
        db.add(user_message)
        db.flush()
        job = ReviewJob(
            job_id=job_id, user_id=user_id, session_id=session_id,
            user_message_id=user_message.id, payload=payload, status="queued", progress=0,
            phase="queued", attempts=0, max_attempts=max(1, min(max_attempts, 3)),
            created_at=now, updated_at=now,
        )
        db.add(job)
        db.flush()
        _new_outbox(db, job_id, 0)
        return _public(job)


def create(user_id: int, session_id: str, user_message_id: int,
           payload: dict, max_attempts: int = 2) -> dict:
    """兼容仓储级测试；API 应优先使用 create_with_message。"""
    now = time.time()
    job = ReviewJob(
        job_id=uuid.uuid4().hex, user_id=user_id, session_id=session_id,
        user_message_id=user_message_id, payload=payload, status="queued", progress=0,
        phase="queued", attempts=0, max_attempts=max(1, min(max_attempts, 3)),
        created_at=now, updated_at=now,
    )
    with session_scope() as db:
        db.add(job)
        db.flush()
        _new_outbox(db, job.job_id, 0)
        return _public(job)


def get(user_id: int, job_id: str) -> dict | None:
    with session_scope() as db:
        job = db.scalar(select(ReviewJob).where(
            ReviewJob.job_id == job_id, ReviewJob.user_id == user_id
        ))
        return _public(job) if job else None


def get_internal(job_id: str) -> dict | None:
    with session_scope() as db:
        job = db.get(ReviewJob, job_id)
        return _public(job, include_payload=True) if job else None


def claim(job_id: str, worker_id: str = "worker", lease_seconds: int = 900) -> dict | None:
    """带行锁领取任务；过期租约可由其他 Worker 接管。"""
    now = time.time()
    with session_scope() as db:
        job = db.scalar(
            select(ReviewJob).where(
                ReviewJob.job_id == job_id,
                or_(
                    ReviewJob.status == "queued",
                    (ReviewJob.status == "running") & (ReviewJob.lease_expires_at < now),
                ),
            ).with_for_update(skip_locked=True)
        )
        if job is None or job.attempts >= job.max_attempts:
            return None
        job.status = "running"
        job.phase = "preparing"
        job.progress = 10
        job.attempts += 1
        job.worker_id = worker_id
        job.lease_expires_at = now + lease_seconds
        job.started_at = now
        job.updated_at = now
        job.error = None
        db.flush()
        return _public(job, include_payload=True)


def update_progress(job_id: str, progress: int, phase: str,
                    lease_seconds: int = 900) -> None:
    now = time.time()
    with session_scope() as db:
        job = db.get(ReviewJob, job_id)
        if job and job.status == "running":
            job.progress = max(0, min(progress, 99))
            job.phase = phase
            job.updated_at = now
            job.lease_expires_at = now + lease_seconds


def complete(job_id: str, agent_result: dict) -> dict | None:
    """助手消息与任务成功状态在同一事务提交；重复投递返回已有结果。"""
    now = time.time()
    with session_scope() as db:
        job = db.scalar(
            select(ReviewJob).where(ReviewJob.job_id == job_id).with_for_update()
        )
        if job is None:
            return None
        if job.status == "succeeded":
            return job.result
        if job.status != "running":
            return None
        assistant = db.scalar(select(Message).where(Message.review_job_id == job_id))
        if assistant is None:
            assistant = Message(
                session_id=job.session_id, role="assistant", content=agent_result["answer"],
                citation=agent_result.get("citation_check"), trace=agent_result.get("trace"),
                review_job_id=job_id,
            )
            db.add(assistant)
            db.flush()
        chat = db.get(ChatSession, job.session_id)
        if chat:
            chat.updated_at = now
        contract_meta = None
        if chat and chat.contract:
            contract_meta = {"name": chat.contract_name or "已附加合同", "chars": len(chat.contract)}
        result = {
            "answer": agent_result["answer"], "session_id": job.session_id,
            "user_id": job.user_message_id, "assistant_id": assistant.id,
            "trace": agent_result.get("trace", []),
            "citation_check": agent_result.get("citation_check", {}),
            "contract": contract_meta,
        }
        job.result = result
        job.status = "succeeded"
        job.progress = 100
        job.phase = "completed"
        job.error = None
        job.finished_at = now
        job.updated_at = now
        job.lease_expires_at = None
        return result


def fail(job_id: str, error: str) -> bool:
    now = time.time()
    with session_scope() as db:
        job = db.scalar(
            select(ReviewJob).where(ReviewJob.job_id == job_id).with_for_update()
        )
        if job is None or job.status != "running":
            return False
        retryable = job.attempts < job.max_attempts
        job.status = "queued" if retryable else "failed"
        job.phase = "retrying" if retryable else "failed"
        job.progress = 0
        job.error = error[:2000]
        job.updated_at = now
        job.finished_at = None if retryable else now
        job.lease_expires_at = None
        if retryable:
            _new_outbox(db, job_id, job.attempts)
        return retryable


def retry(user_id: int, job_id: str) -> dict | None:
    now = time.time()
    with session_scope() as db:
        job = db.scalar(select(ReviewJob).where(
            ReviewJob.job_id == job_id, ReviewJob.user_id == user_id
        ).with_for_update())
        if job is None or job.status != "failed":
            return None
        job.status, job.phase, job.progress = "queued", "queued", 0
        job.error = None
        job.result = None
        job.finished_at = None
        job.max_attempts = job.attempts + 1
        job.updated_at = now
        _new_outbox(db, job_id, job.attempts)
        return _public(job)


def pending_outbox(publisher_id: str = "publisher", limit: int = 100,
                   claim_seconds: int = 30) -> list[dict]:
    """通过短租约 + SKIP LOCKED 领取 Outbox，避免多个 API 实例重复发布。"""
    now = time.time()
    with session_scope() as db:
        rows = db.scalars(
            select(TaskOutbox).where(
                TaskOutbox.published.is_(False),
                or_(TaskOutbox.claimed_at.is_(None), TaskOutbox.claimed_at < now - claim_seconds),
            ).order_by(TaskOutbox.id).limit(limit).with_for_update(skip_locked=True)
        ).all()
        for row in rows:
            row.claimed_at = now
            row.publisher_id = publisher_id
        return [{"id": row.id, "job_id": row.job_id, "attempt": row.attempt} for row in rows]


def mark_published(outbox_id: int) -> None:
    with session_scope() as db:
        row = db.get(TaskOutbox, outbox_id)
        if row and not row.published:
            row.published = True
            row.published_at = time.time()
            row.claimed_at = None
            row.publisher_id = None


def release_outbox(outbox_id: int) -> None:
    with session_scope() as db:
        row = db.get(TaskOutbox, outbox_id)
        if row and not row.published:
            row.claimed_at = None
            row.publisher_id = None


def recover_incomplete() -> list[str]:
    """只回收租约已过期的 running；绝不干扰其他实例仍在执行的任务。"""
    now = time.time()
    with session_scope() as db:
        recovered = []
        stale = db.scalars(select(ReviewJob).where(
            ReviewJob.status == "running", ReviewJob.lease_expires_at < now
        ).with_for_update(skip_locked=True)).all()
        for job in stale:
            if job.attempts < job.max_attempts:
                job.status, job.phase, job.progress = "queued", "recovered", 0
                job.lease_expires_at = None
                _new_outbox(db, job.job_id, job.attempts)
                recovered.append(job.job_id)
            else:
                job.status, job.phase = "failed", "lease_expired"
                job.error = "Worker 租约过期且已耗尽重试次数"
                job.finished_at = now
        return recovered
