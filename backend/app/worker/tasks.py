# -*- coding: utf-8 -*-
"""独立 Worker 中执行合同审查；数据库状态机负责幂等与重试预算。"""
import os
import logging
import random

from .celery_app import celery_app
from ..agent.loop import run
from ..infra import review_jobs
from ..infra import review_events
from ..infra.observability import REVIEW_JOBS
from ..infra.semaphore import SemaphoreBusy, SemaphoreUnavailable, leased_semaphore

log = logging.getLogger(__name__)


@celery_app.task(name="legal_rag.review.execute", bind=True, max_retries=None)
def execute_review(self, job_id: str) -> dict:
    preview = review_jobs.get_internal(job_id)
    if preview is None:
        return {"job_id": job_id, "status": "ignored"}
    worker_id = f"{os.environ.get('HOSTNAME', 'worker')}:{self.request.id}"
    lease = int(os.environ.get("REVIEW_JOB_LEASE_SECONDS", "3600"))
    try:
        with leased_semaphore(
            f"ai:semaphore:user:{preview['user_id']}",
            int(os.environ.get("USER_REVIEW_CONCURRENCY", "3")),
            lease_seconds=int(os.environ.get("USER_SEMAPHORE_LEASE_SECONDS", "90")),
            wait_seconds=float(os.environ.get("USER_SEMAPHORE_WAIT_SECONDS", "2")),
            scope="user_review",
        ):
            job = review_jobs.claim(job_id, worker_id=worker_id, lease_seconds=lease)
            if job is None:
                return {"job_id": job_id, "status": "ignored"}
            review_events.publish(job_id, "started", progress=10, phase="preparing")
            log.info("review_job_started", extra={"job_id": job_id, "user_id": job["user_id"]})
            try:
                review_jobs.update_progress(job_id, 35, "reviewing", lease_seconds=lease)
                review_events.publish(job_id, "reviewing", progress=35, phase="reviewing")
                result = run(
                    job["payload"]["message"],
                    history=job["payload"]["history"],
                    progress_callback=lambda trace: review_events.publish(
                        job_id, "agent_trace", progress=60, phase="reviewing", trace=trace
                    ),
                )
                review_jobs.update_progress(job_id, 90, "persisting", lease_seconds=lease)
                review_events.publish(job_id, "persisting", progress=90, phase="persisting")
                review_jobs.complete(job_id, result)
                review_events.publish(job_id, "completed", progress=100, phase="completed")
                REVIEW_JOBS.labels("succeeded").inc()
                log.info("review_job_succeeded", extra={"job_id": job_id, "user_id": job["user_id"]})
                return {"job_id": job_id, "status": "succeeded"}
            except Exception as exc:  # noqa: BLE001 - 先可靠落失败状态，再决定重试
                retryable = review_jobs.fail(job_id, f"{type(exc).__name__}: {exc}")
                event = "retrying" if retryable else "failed"
                review_events.publish(job_id, event, progress=0, phase=event)
                REVIEW_JOBS.labels(event).inc()
                log.exception(
                    "review_job_failed",
                    extra={"job_id": job_id, "user_id": job["user_id"]},
                )
                if retryable:
                    from ..services.review_tasks import dispatch_pending
                    dispatch_pending()
                return {"job_id": job_id, "status": event}
    except (SemaphoreBusy, SemaphoreUnavailable) as exc:
        # 尚未 claim，不消耗数据库 attempts；让 Celery 延迟重投，避免并发满时误判业务失败。
        raise self.retry(exc=exc, countdown=random.randint(2, 5))
