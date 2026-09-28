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


def test_async_jobs_required_in_production_without_explicit_flag(monkeypatch):
    """Production forces async jobs even when REQUIRE_ASYNC_JOBS was left unset."""
    monkeypatch.setattr(settings, "require_async_jobs", False)
    monkeypatch.setattr(settings, "app_env", "production")
    assert settings.async_jobs_required is True

    monkeypatch.setattr(settings, "app_env", "development")
    assert settings.async_jobs_required is False

    monkeypatch.setattr(settings, "require_async_jobs", True)
    assert settings.async_jobs_required is True


def test_async_jobs_default_false_outside_prod_compose(monkeypatch):
    """Local/dev compose leaves REQUIRE_ASYNC_JOBS unset → sync generate_baseline OK."""
    monkeypatch.setattr(settings, "require_async_jobs", False)
    for env in ("development", "test", "staging"):
        monkeypatch.setattr(settings, "app_env", env)
        assert settings.async_jobs_required is False, env


def test_driver_context_preload_batches_lookups(db_session, seed_line_items):
    """Review dashboard driver context loads deps/forecasts once, not per line item."""
    from app.api.dashboard import _build_driver_context, _preload_driver_context
    from app.models.forecast import ForecastLineResult, ForecastVersion
    from app.models.line_item import LineItemDependency

    rev = seed_line_items["REV-001"]
    svc = seed_line_items["REV-002"]
    db_session.add(
        LineItemDependency(
            dependent_item_id=rev.id,
            source_item_id=svc.id,
            relationship_type="sum",
            weight=1.0,
        )
    )
    version = ForecastVersion(
        id="preload-v1",
        name="Preload",
        status="draft",
        version_type="baseline",
        horizon_months=3,
        scenario="base",
    )
    db_session.add(version)
    db_session.flush()
    for period, p50 in (("2026-01", 100.0), ("2026-02", 110.0)):
        db_session.add(
            ForecastLineResult(
                id=f"flr-{svc.id}-{period}",
                version_id=version.id,
                line_item_id=svc.id,
                period=period,
                p10=90.0,
                p50=p50,
                p90=120.0,
                model_type="linear",
                confidence_score=80.0,
                confidence_level="high",
            )
        )
    db_session.commit()

    cache = _preload_driver_context(db_session, version.id, [rev.id, svc.id])
    assert svc.id in cache["forecast_totals"]
    assert cache["forecast_totals"][svc.id] == pytest.approx(210.0)

    ctx = _build_driver_context(
        rev,
        [],
        {},
        db_session,
        version.id,
        cache=cache,
    )
    assert any(d["name"] == svc.name for d in ctx["dependencies"])
    assert any(d["forecast_total"] == pytest.approx(210.0) for d in ctx["dependencies"])


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
