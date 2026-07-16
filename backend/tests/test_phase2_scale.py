"""Phase 2 scale tests — N+1 batching, panel pagination, async jobs, chunked ingest."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import settings


@pytest.fixture
def client():
    import os

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


def test_budget_bridge_pagination_shape(client):
    """Budget bridge returns paginated rows with grouped aggregates."""
    login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    assert login.status_code == 200
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    latest = client.get("/api/executive/latest", headers=headers)
    assert latest.status_code == 200
    version = latest.json().get("version")
    if not version:
        pytest.skip("No published version in test DB")

    r = client.get(
        f"/api/executive/budget-bridge/{version['id']}?page_size=5",
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "rows" in body
    assert body["page_size"] == 5
    assert "total" in body


def test_forecast_table_pagination(client):
    login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    assert login.status_code == 200
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    from app.database import SessionLocal
    from app.models.forecast import ForecastVersion

    db = SessionLocal()
    try:
        v = db.query(ForecastVersion).first()
        if not v:
            pytest.skip("No forecast version")
        vid = v.id
    finally:
        db.close()

    r = client.get(
        f"/api/panel/forecast-table/{vid}?limit=2&offset=0",
        headers=headers,
    )
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert "has_more" in data
    assert data["limit"] == 2
    assert data["offset"] == 0
    assert len(data["rows"]) <= 2
    assert "quality_summary" in data
    assert "total_count" in data


@pytest.mark.asyncio
async def test_require_async_jobs_fails_without_redis(monkeypatch):
    monkeypatch.setattr(settings, "require_async_jobs", True)
    monkeypatch.setattr(settings, "redis_url", "")

    from app.services.job_queue import enqueue_generate_baseline

    job = await enqueue_generate_baseline(
        version_name="t",
        params={},
        user_id="u1",
    )
    assert job["status"] == "sync_required"
    assert "Redis" in job["message"] or "arq" in job["message"].lower()


@pytest.mark.asyncio
async def test_csv_chunked_read(tmp_path: Path):
    from app.services.ingestion.csv_adapter import CSVActualsProvider

    path = tmp_path / "actuals.csv"
    lines = ["account_code,account_name,period,value"]
    for i in range(12):
        lines.append(f"A{i % 3},Account {i % 3},2024-{(i % 12) + 1:02d},{100 + i}")
    path.write_text("\n".join(lines))

    provider = CSVActualsProvider()
    result = await provider.pull_actuals({"file_path": str(path), "chunk_size": 5})
    assert result.success, result.error
    assert result.row_count == 12
    assert result.metadata.get("chunks_read", 0) >= 2
