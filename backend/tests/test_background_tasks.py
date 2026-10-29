"""Slow request work moved onto a background queue (enhancement plan 3.2)."""

from __future__ import annotations

import io
import os

import pytest
from fastapi.testclient import TestClient

from app.services import background_tasks as bt


@pytest.fixture
def client():
    os.environ["SEED_DEMO_USERS"] = "true"
    os.environ["APP_ENV"] = "test"
    from app.main import app
    from app.rate_limit import limiter

    try:
        limiter.reset()
    except Exception:
        pass
    with TestClient(app) as c:
        yield c


def _h(client, username="admin", password="admin"):
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


class _FakePool:
    """Stands in for arq's Redis pool: records what was enqueued."""

    jobs: list[tuple] = []

    async def enqueue_job(self, fn, *args, **kwargs):
        _FakePool.jobs.append((fn, args, kwargs))

    async def aclose(self):
        pass


@pytest.fixture
def fake_queue(monkeypatch):
    """Redis 'configured', arq enqueue captured, job records kept in memory."""
    import arq

    _FakePool.jobs = []

    async def create_pool(*_a, **_k):
        return _FakePool()

    monkeypatch.setattr(arq, "create_pool", create_pool)
    monkeypatch.setattr(bt, "redis_configured", lambda: True)
    monkeypatch.setattr("app.services.job_queue._redis", lambda: None)
    monkeypatch.setattr(bt.settings, "redis_url", "redis://fake:6379/0")
    return _FakePool


def test_worker_queues_are_split():
    from app.workers.arq_worker import TaskWorkerSettings, WorkerSettings

    assert TaskWorkerSettings.queue_name == bt.TASK_QUEUE
    assert TaskWorkerSettings.max_jobs > 1
    assert WorkerSettings.max_jobs == 1  # forecasts keep their own slot
    assert {f.__name__ for f in TaskWorkerSettings.functions} == {"run_background_task"}


def test_should_run_async(monkeypatch):
    monkeypatch.setattr(bt, "redis_configured", lambda: False)
    monkeypatch.setattr(bt.settings, "require_async_jobs", False)
    monkeypatch.setattr(bt.settings, "app_env", "test")
    assert bt.should_run_async(None) is False
    assert bt.should_run_async(True) is True
    monkeypatch.setattr(bt, "redis_configured", lambda: True)
    assert bt.should_run_async(None) is True
    assert bt.should_run_async(False) is False


def test_inline_by_default_without_a_queue(client):
    r = client.post("/api/panel/rescore-all", headers=_h(client))
    assert r.status_code == 200, r.text
    assert r.json()["success"] is True


def test_queued_when_a_queue_exists(client, fake_queue):
    r = client.post("/api/panel/rescore-all?force=true", headers=_h(client))
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["status"] == "queued" and body["kind"] == "rescore_all"
    fn, args, kwargs = fake_queue.jobs[-1]
    assert fn == "run_background_task"
    assert args[1:3] == ("rescore_all", {"force": True})
    assert kwargs["_queue_name"] == bt.TASK_QUEUE

    job = client.get(f"/api/jobs/{body['job_id']}", headers=_h(client)).json()
    assert job["status"] == "queued"


def test_async_requested_without_queue_is_503(client, monkeypatch):
    monkeypatch.setattr(bt, "redis_configured", lambda: False)
    r = client.post("/api/panel/rescore-all?async_job=true", headers=_h(client))
    assert r.status_code == 503


def test_document_upload_saves_then_indexes_in_background(client, fake_queue):
    files = {"file": ("notes.txt", io.BytesIO(b"Revenue grew on pricing."), "text/plain")}
    r = client.post("/api/context/upload", files=files, headers=_h(client))
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["status"] == "processing" and body["job_id"]
    _, args, _ = fake_queue.jobs[-1]
    assert args[1] == "document_embedding" and args[2]["document_id"] == body["id"]


def test_document_left_failed_not_processing_when_queue_missing(client, monkeypatch):
    from app.database import SessionLocal
    from app.models.document import Document

    monkeypatch.setattr(bt, "redis_configured", lambda: False)
    files = {"file": ("notes.txt", io.BytesIO(b"x"), "text/plain")}
    r = client.post("/api/context/upload?async_job=true", files=files, headers=_h(client))
    assert r.status_code == 503
    db = SessionLocal()
    try:
        doc = db.query(Document).order_by(Document.created_at.desc()).first()
        assert doc.status == "failed"
    finally:
        db.close()


def test_distribute_checks_version_access_before_queuing(client, fake_queue):
    r = client.post(
        "/api/executive/distribute",
        json={"version_id": "does-not-exist", "recipients": ["cfo@example.com"]},
        headers=_h(client),
    )
    assert r.status_code == 404
    assert fake_queue.jobs == []


def _seed_job(job_id: str, kind: str, user_id: str | None):
    from app.services.job_queue import _save

    _save({"job_id": job_id, "kind": kind, "status": "queued", "user_id": user_id})


@pytest.mark.asyncio
async def test_worker_runs_task_and_notifies(client, monkeypatch):
    from app.database import SessionLocal
    from app.models.notification import Notification
    from app.models.user import User
    from app.services.job_queue import get_job

    monkeypatch.setattr("app.services.job_queue._redis", lambda: None)
    db = SessionLocal()
    admin_id = db.query(User).filter(User.username == "admin").first().id
    db.close()

    _seed_job("bt-ok", "rescore_all", admin_id)
    result = await bt.execute_task("bt-ok", "rescore_all", {"force": False}, admin_id)
    assert result["success"] is True
    assert get_job("bt-ok")["status"] == "completed"

    db = SessionLocal()
    try:
        n = db.query(Notification).filter(Notification.entity_id == "bt-ok").one()
        assert n.kind == "job_finished" and n.user_id == admin_id
    finally:
        db.close()


@pytest.mark.asyncio
async def test_worker_failure_is_recorded_and_notified(client, monkeypatch):
    from app.database import SessionLocal
    from app.models.notification import Notification
    from app.models.user import User
    from app.services.job_queue import get_job

    monkeypatch.setattr("app.services.job_queue._redis", lambda: None)

    async def boom(db, actor, params):
        raise RuntimeError("warehouse unreachable")

    monkeypatch.setitem(bt._HANDLERS, "integration_pull", boom)
    db = SessionLocal()
    admin_id = db.query(User).filter(User.username == "admin").first().id
    db.close()

    _seed_job("bt-fail", "integration_pull", admin_id)
    with pytest.raises(RuntimeError):
        await bt.execute_task("bt-fail", "integration_pull", {}, admin_id)
    job = get_job("bt-fail")
    assert job["status"] == "failed" and "unreachable" in job["error"]
    db = SessionLocal()
    try:
        assert db.query(Notification).filter(Notification.entity_id == "bt-fail").one().kind == "job_failed"
    finally:
        db.close()


@pytest.mark.asyncio
async def test_worker_job_for_missing_user_runs_with_no_access(monkeypatch):
    seen = {}

    async def probe(db, actor, params):
        from app.models.forecast import ForecastVersion

        seen["actor"] = actor
        seen["versions"] = db.query(ForecastVersion).count()
        return {}

    monkeypatch.setitem(bt._HANDLERS, "rescore_all", probe)
    monkeypatch.setattr("app.services.job_queue._redis", lambda: None)
    _seed_job("bt-ghost", "rescore_all", "no-such-user")
    await bt.execute_task("bt-ghost", "rescore_all", {}, "no-such-user")
    assert seen == {"actor": None, "versions": 0}


def test_worker_startup_hooks_are_awaitable():
    """arq awaits on_startup/on_shutdown; a plain function crashes the worker at boot."""
    import inspect

    from app.workers.arq_worker import TaskWorkerSettings, WorkerSettings

    for settings_cls in (WorkerSettings, TaskWorkerSettings):
        for hook in ("on_startup", "on_shutdown"):
            fn = getattr(settings_cls, hook, None)
            if fn is not None:
                assert inspect.iscoroutinefunction(fn), f"{settings_cls.__name__}.{hook} must be async"
