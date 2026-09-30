import os
import tempfile
from pathlib import Path

_tmp = tempfile.TemporaryDirectory()
os.environ.pop("DATABASE_URL", None)
os.environ.pop("REDIS_URL", None)
os.environ.pop("CELERY_BROKER_URL", None)
os.environ["SESSION_DB"] = str(Path(_tmp.name) / "api-test.db")
os.environ["INIT_PASSWORD"] = "test-password-123"
os.environ["JWT_SECRET"] = "test-jwt-secret-at-least-32-characters"
os.environ["APP_ENV"] = "test"

from fastapi.testclient import TestClient

from backend.app.api.main import app
from backend.app.infra import review_jobs, session
from backend.app.services import review_tasks


def teardown_module():
    from backend.app.infra.database import get_engine
    get_engine().dispose()
    _tmp.cleanup()


def auth_headers(client: TestClient, username: str) -> dict:
    response = client.post("/api/register", json={
        "username": username, "password": "strong-password-123",
    })
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def test_health_auth_review_and_isolation(monkeypatch):
    monkeypatch.setattr(review_tasks, "submit_job", lambda job_id: None)
    monkeypatch.setattr(review_tasks, "recover_jobs", lambda: 0)
    monkeypatch.setattr(review_tasks, "start_dispatcher", lambda: None)
    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/ready").status_code == 200
        alice = auth_headers(client, "alice")
        bob = auth_headers(client, "bob")
        created = client.post("/api/reviews", headers=alice, json={
            "message": "请审查违约金条款",
            "session_id": "api-review-session",
            "contract": "违约金为合同金额的百分之五十。",
        })
        assert created.status_code == 202, created.text
        job_id = created.json()["job_id"]
        assert client.get(f"/api/reviews/{job_id}", headers=alice).status_code == 200
        assert client.get(f"/api/reviews/{job_id}", headers=bob).status_code == 404


def test_job_completion_is_idempotent(monkeypatch):
    monkeypatch.setattr(review_tasks, "submit_job", lambda job_id: None)
    monkeypatch.setattr(review_tasks, "recover_jobs", lambda: 0)
    monkeypatch.setattr(review_tasks, "start_dispatcher", lambda: None)
    with TestClient(app) as client:
        headers = auth_headers(client, "charlie")
        created = client.post("/api/reviews", headers=headers, json={
            "message": "审查合同", "session_id": "idempotent-session",
        }).json()
        job = review_jobs.claim(created["job_id"], worker_id="test")
        result = {"answer": "完成", "trace": [], "citation_check": {"total": 0}}
        first = review_jobs.complete(job["job_id"], result)
        second = review_jobs.complete(job["job_id"], result)
        assert first == second
        history = session.get_history(job["user_id"], job["session_id"])
        assert len([m for m in history if m["role"] == "assistant"]) == 1


def test_retry_endpoint_and_upload_limit(monkeypatch):
    monkeypatch.setattr(review_tasks, "submit_job", lambda job_id: None)
    monkeypatch.setattr(review_tasks, "recover_jobs", lambda: 0)
    monkeypatch.setattr(review_tasks, "start_dispatcher", lambda: None)
    with TestClient(app) as client:
        headers = auth_headers(client, "dave")
        created = client.post("/api/reviews", headers=headers, json={
            "message": "审查失败重试", "session_id": "retry-session", "max_attempts": 1,
        }).json()
        review_jobs.claim(created["job_id"], worker_id="test")
        review_jobs.fail(created["job_id"], "simulated")
        retried = client.post(f"/api/reviews/{created['job_id']}/retry", headers=headers)
        assert retried.status_code == 202 and retried.json()["status"] == "queued"

        monkeypatch.setenv("MAX_UPLOAD_BYTES", "4")
        oversized = client.post(
            "/api/upload", headers=headers, files={"file": ("contract.txt", b"12345")}
        )
        assert oversized.status_code == 413
        assert client.get("/metrics").status_code == 200


def test_celery_worker_path(monkeypatch):
    from backend.app.worker import tasks

    monkeypatch.setattr(review_tasks, "submit_job", lambda job_id: None)
    monkeypatch.setattr(review_tasks, "recover_jobs", lambda: 0)
    monkeypatch.setattr(review_tasks, "start_dispatcher", lambda: None)
    monkeypatch.setattr(tasks, "run", lambda message, history: {
        "answer": "worker 完成", "trace": [], "citation_check": {"total": 0},
    })
    with TestClient(app) as client:
        headers = auth_headers(client, "erin")
        created = client.post("/api/reviews", headers=headers, json={
            "message": "通过 Worker 审查", "session_id": "worker-session",
        }).json()
        outcome = tasks.execute_review.run(created["job_id"])
        assert outcome["status"] == "succeeded"
        done = client.get(f"/api/reviews/{created['job_id']}", headers=headers).json()
        assert done["status"] == "succeeded" and done["result"]["answer"] == "worker 完成"
