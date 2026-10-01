# -*- coding: utf-8 -*-
"""结构化日志、请求链路标识和 Prometheus 指标。"""
import json
import logging
import sys
import time
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone

from prometheus_client import Counter, Gauge, Histogram

request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")

HTTP_REQUESTS = Counter(
    "legal_rag_http_requests_total", "HTTP requests", ["method", "route", "status"]
)
HTTP_LATENCY = Histogram(
    "legal_rag_http_request_duration_seconds", "HTTP request latency", ["method", "route"]
)
REVIEW_JOBS = Counter(
    "legal_rag_review_jobs_total", "Review job outcomes", ["status"]
)
LLM_REQUESTS = Counter(
    "legal_rag_llm_requests_total", "LLM requests", ["model", "status"]
)
LLM_LATENCY = Histogram(
    "legal_rag_llm_request_duration_seconds", "LLM request latency", ["model"]
)
LLM_TOKENS = Counter(
    "legal_rag_llm_tokens_total", "LLM token usage", ["model", "kind"]
)
REVIEW_EVENTS = Counter(
    "legal_rag_review_events_total", "Review runtime events", ["event"]
)
SSE_CONNECTIONS = Gauge(
    "legal_rag_sse_connections", "Active review SSE connections"
)
SEMAPHORE_WAIT = Histogram(
    "legal_rag_semaphore_wait_seconds", "Distributed semaphore wait time", ["scope"]
)
SEMAPHORE_IN_USE = Gauge(
    "legal_rag_semaphore_in_use", "Leases held by this process", ["scope"]
)
REDIS_FAILURES = Counter(
    "legal_rag_redis_failures_total", "Redis runtime-control failures", ["operation"]
)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", request_id_ctx.get()),
        }
        for name in ("job_id", "user_id", "phase", "duration_ms"):
            value = getattr(record, name, None)
            if value is not None:
                payload[name] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging() -> None:
    root = logging.getLogger()
    if any(getattr(h, "_legal_rag_json", False) for h in root.handlers):
        return
    handler = logging.StreamHandler(sys.stdout)
    handler._legal_rag_json = True
    handler.setFormatter(JsonFormatter())
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)


async def request_metrics_middleware(request, call_next):
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
    token = request_id_ctx.set(request_id)
    started = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        route_obj = request.scope.get("route")
        route = getattr(route_obj, "path", request.url.path)
        duration = time.perf_counter() - started
        HTTP_REQUESTS.labels(request.method, route, str(status)).inc()
        HTTP_LATENCY.labels(request.method, route).observe(duration)
        logging.getLogger("http").info(
            "request_completed",
            extra={"duration_ms": round(duration * 1000, 2)},
        )
        request_id_ctx.reset(token)
