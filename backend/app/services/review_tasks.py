# -*- coding: utf-8 -*-
"""Transactional Outbox → RabbitMQ 发布器。"""
import logging
import os
import threading
import uuid

from ..infra import review_jobs

log = logging.getLogger(__name__)
_stop = threading.Event()
_thread = None
_publisher_id = f"{os.environ.get('HOSTNAME', 'api')}:{uuid.uuid4().hex[:8]}"


def dispatch_pending() -> int:
    """发布未发送 Outbox；发布成功后才标记，崩溃时允许安全重复投递。"""
    from ..worker.tasks import execute_review

    published = 0
    for item in review_jobs.pending_outbox(publisher_id=_publisher_id):
        try:
            execute_review.apply_async(
                args=[item["job_id"]],
                task_id=f"review-{item['job_id']}-{item['attempt']}",
                delivery_mode=2,
            )
            review_jobs.mark_published(item["id"])
            published += 1
        except Exception:  # noqa: BLE001 - 保留 outbox，下一轮继续投递
            review_jobs.release_outbox(item["id"])
            log.exception("review_job_publish_failed", extra={"job_id": item["job_id"]})
    return published


def submit_job(job_id: str) -> None:
    # job_id 已存在于 Outbox；参数保留兼容旧调用。
    dispatch_pending()


def recover_jobs() -> int:
    review_jobs.recover_incomplete()
    return dispatch_pending()


def _loop(interval: float) -> None:
    while not _stop.wait(interval):
        review_jobs.recover_incomplete()
        dispatch_pending()


def start_dispatcher(interval: float = 2.0) -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(
        target=_loop, args=(interval,), name="review-outbox-dispatcher", daemon=True
    )
    _thread.start()


def stop_dispatcher() -> None:
    _stop.set()
