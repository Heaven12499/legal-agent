# -*- coding: utf-8 -*-
import os
from urllib.parse import quote

from celery import Celery
from celery.signals import worker_ready
from prometheus_client import start_http_server

from ..infra.observability import configure_logging

configure_logging()


def broker_url() -> str:
    if os.environ.get("CELERY_BROKER_URL"):
        return os.environ["CELERY_BROKER_URL"]
    user = quote(os.environ.get("RABBITMQ_USER", "guest"), safe="")
    password = quote(os.environ.get("RABBITMQ_PASSWORD", "guest"), safe="")
    host = os.environ.get("RABBITMQ_HOST", "localhost")
    port = os.environ.get("RABBITMQ_PORT", "5672")
    return f"amqp://{user}:{password}@{host}:{port}//"

celery_app = Celery(
    "legal_rag",
    broker=broker_url(),
    include=["backend.app.worker.tasks"],
)
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_track_started=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    broker_connection_timeout=3,
    broker_transport_options={"confirm_publish": True},
    task_routes={"legal_rag.review.execute": {"queue": "contract_review"}},
    task_default_queue="contract_review",
    timezone="Asia/Shanghai",
    enable_utc=True,
    worker_hijack_root_logger=False,
    task_soft_time_limit=int(os.environ.get("REVIEW_SOFT_TIME_LIMIT", "1200")),
    task_time_limit=int(os.environ.get("REVIEW_HARD_TIME_LIMIT", "1260")),
)


@worker_ready.connect
def _start_worker_metrics(**_kwargs):
    port = int(os.environ.get("WORKER_METRICS_PORT", "9101"))
    start_http_server(port)
