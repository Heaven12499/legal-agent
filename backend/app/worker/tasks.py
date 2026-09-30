# -*- coding: utf-8 -*-
"""独立 Worker 中执行合同审查；数据库状态机负责幂等与重试预算。"""
import os
import logging

from .celery_app import celery_app
from ..agent.loop import run
from ..infra import review_jobs
from ..infra.observability import REVIEW_JOBS

log = logging.getLogger(__name__)


@celery_app.task(name="legal_rag.review.execute", bind=True)
def execute_review(self, job_id: str) -> dict:
    worker_id = f"{os.environ.get('HOSTNAME', 'worker')}:{self.request.id}"
    lease = int(os.environ.get("REVIEW_JOB_LEASE_SECONDS", "3600"))
    job = review_jobs.claim(job_id, worker_id=worker_id, lease_seconds=lease)
    if job is None:
        return {"job_id": job_id, "status": "ignored"}
    log.info("review_job_started", extra={"job_id": job_id, "user_id": job["user_id"]})
    try:
        review_jobs.update_progress(job_id, 35, "reviewing", lease_seconds=lease)
        result = run(job["payload"]["message"], history=job["payload"]["history"])
        review_jobs.update_progress(job_id, 90, "persisting", lease_seconds=lease)
        review_jobs.complete(job_id, result)
        REVIEW_JOBS.labels("succeeded").inc()
        log.info("review_job_succeeded", extra={"job_id": job_id, "user_id": job["user_id"]})
        return {"job_id": job_id, "status": "succeeded"}
    except Exception as exc:  # noqa: BLE001 - 先可靠落失败状态，再让下一条消息重试
        retryable = review_jobs.fail(job_id, f"{type(exc).__name__}: {exc}")
        REVIEW_JOBS.labels("retrying" if retryable else "failed").inc()
        log.exception(
            "review_job_failed",
            extra={"job_id": job_id, "user_id": job["user_id"]},
        )
        if retryable:
            from ..services.review_tasks import dispatch_pending
            dispatch_pending()
        return {"job_id": job_id, "status": "retrying" if retryable else "failed"}
