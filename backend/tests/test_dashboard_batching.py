"""Regression tests for dashboard N+1 batching (driver-inputs + accuracy live join)."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, inspect, text
from sqlalchemy.engine import Engine

from app.models.actuals import ActualsRecord
from app.models.forecast import ForecastLineResult, ForecastVersion


def _ensure_phase1_columns() -> None:
    """Persistent test_app.db may predate migration 013; create_all won't ALTER."""
    from app.database import engine

    fv_cols = {c["name"] for c in inspect(engine).get_columns("forecast_versions")}
    flr_cols = {c["name"] for c in inspect(engine).get_columns("forecast_line_results")}
    with engine.begin() as conn:
        if "selection_rule" not in fv_cols:
            conn.execute(text("ALTER TABLE forecast_versions ADD COLUMN selection_rule VARCHAR(64)"))
        if "model_mase" not in flr_cols:
            conn.execute(text("ALTER TABLE forecast_line_results ADD COLUMN model_mase FLOAT"))
        if "model_pinball" not in flr_cols:
            conn.execute(text("ALTER TABLE forecast_line_results ADD COLUMN model_pinball FLOAT"))


def _count_queries(engine: Engine):
    """Count SQL statements executed on `engine` while attached."""
    state = {"n": 0}

    def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        state["n"] += 1

    event.listen(engine, "before_cursor_execute", before_cursor_execute)

    class Counter:
        @property
        def count(self) -> int:
            return state["n"]

        def reset(self) -> None:
            state["n"] = 0

        def remove(self) -> None:
            event.remove(engine, "before_cursor_execute", before_cursor_execute)

    return Counter()


@pytest.fixture
def client():
    import os

    os.environ["SEED_DEMO_USERS"] = "true"
    os.environ["APP_ENV"] = "test"
    _ensure_phase1_columns()
    from app.main import app
    from app.rate_limit import limiter

    try:
        limiter.reset()
    except Exception:
        pass

    with TestClient(app) as c:
        yield c


def test_driver_inputs_batches_forecast_and_actuals(
    db_engine, db_session, seed_users, seed_line_items, seed_actuals
):
    """Available line items must not issue 2 queries per row."""
    from app.api.dashboard import get_driver_inputs

    version = ForecastVersion(
        id="drv-batch-v1",
        name="Driver Batch",
        status="draft",
        version_type="baseline",
        horizon_months=3,
        scenario="base",
    )
    db_session.add(version)
    db_session.flush()
    for li in list(seed_line_items.values())[:4]:
        if li.is_calculated:
            continue
        db_session.add(
            ForecastLineResult(
                id=f"flr-drv-{li.id}",
                version_id=version.id,
                line_item_id=li.id,
                period="2024-01",
                p10=90.0,
                p50=100.0,
                p90=110.0,
                model_type="linear",
                confidence_score=75.0,
                confidence_level="medium",
            )
        )
    db_session.commit()

    admin = seed_users["admin"]
    counter = _count_queries(db_engine)
    try:
        counter.reset()
        result = asyncio.run(
            get_driver_inputs(version_id=version.id, current_user=admin, db=db_session)
        )
        queries = counter.count
    finally:
        counter.remove()

    items = result.data["available_line_items"]
    assert len(items) >= 1
    # Must stay O(1) relative to line-item count (not ~2N).
    assert queries < 20, f"expected batched lookups, saw {queries} queries for {len(items)} items"
    assert any(i.get("model_suggested_value") is not None for i in items)
    assert any(i.get("last_actual") is not None for i in items)


def test_accuracy_tracking_live_fallback_uses_join(
    db_engine, db_session, seed_users, seed_line_items, seed_actuals
):
    """Live accuracy path joins actuals once instead of querying per forecast row."""
    from app.api.dashboard import get_accuracy_tracking

    version = ForecastVersion(
        id="acc-batch-v1",
        name="Accuracy Batch",
        status="draft",
        version_type="baseline",
        horizon_months=3,
        scenario="base",
    )
    db_session.add(version)
    db_session.flush()

    # Align forecast periods with seeded actuals (no vintage accuracy rows → live join)
    sample_actuals = (
        db_session.query(ActualsRecord)
        .order_by(ActualsRecord.period)
        .limit(6)
        .all()
    )
    assert sample_actuals, "seed_actuals should provide records"
    for act in sample_actuals:
        db_session.add(
            ForecastLineResult(
                id=f"flr-acc-{act.line_item_id}-{act.period}",
                version_id=version.id,
                line_item_id=act.line_item_id,
                period=act.period,
                p10=act.value * 0.9,
                p50=act.value * 1.05,
                p90=act.value * 1.2,
                model_type="linear",
                confidence_score=70.0,
                confidence_level="medium",
            )
        )
    db_session.commit()

    admin = seed_users["admin"]
    counter = _count_queries(db_engine)
    try:
        counter.reset()
        result = asyncio.run(
            get_accuracy_tracking(version_id=version.id, current_user=admin, db=db_session)
        )
        queries = counter.count
    finally:
        counter.remove()

    assert result.data["overall"]["total_comparisons"] >= 1
    # Even with trend queries, must not scale with ~N per-row actual lookups
    assert queries < 40, f"expected joined live fallback, saw {queries} queries"
    items = result.data.get("items") or []
    if items:
        assert items[0].get("source") == "live"


def test_review_executive_anomaly_with_seeded_results(
    db_session, seed_users, seed_line_items, seed_actuals
):
    """Cover the large review/executive/anomaly builders with seeded forecast rows."""
    from app.api.dashboard import (
        get_anomaly_dashboard,
        get_executive_dashboard,
        get_review_dashboard,
    )

    version = ForecastVersion(
        id="dash-cov-v1",
        name="Dashboard Coverage",
        status="draft",
        version_type="baseline",
        horizon_months=3,
        scenario="base",
        actuals_dataset_id=seed_actuals.id,
    )
    db_session.add(version)
    db_session.flush()

    for li in seed_line_items.values():
        if li.is_calculated:
            continue
        for month, mult in (("2025-10", 1.0), ("2025-11", 1.05), ("2025-12", 1.1)):
            db_session.add(
                ForecastLineResult(
                    id=f"flr-cov-{li.id}-{month}",
                    version_id=version.id,
                    line_item_id=li.id,
                    period=month,
                    p10=80_000 * mult,
                    p50=100_000 * mult,
                    p90=120_000 * mult,
                    model_type="linear",
                    confidence_score=55.0 if li.account_code.startswith("OPEX") else 82.0,
                    confidence_level="medium" if li.account_code.startswith("OPEX") else "high",
                )
            )
    db_session.commit()

    admin = seed_users["admin"]
    review = asyncio.run(
        get_review_dashboard(version_id=version.id, current_user=admin, db=db_session)
    )
    assert review.panel_type == "review_dashboard"
    assert review.data["version"]["id"] == version.id
    assert "buckets" in review.data or "summary" in review.data

    executive = asyncio.run(
        get_executive_dashboard(version_id=version.id, current_user=admin, db=db_session)
    )
    assert executive.panel_type == "executive_dashboard"
    assert executive.data["version"]["id"] == version.id

    anomaly = asyncio.run(
        get_anomaly_dashboard(version_id=version.id, current_user=admin, db=db_session)
    )
    assert anomaly.panel_type == "anomaly_dashboard"
    assert anomaly.data["version"]["id"] == version.id


def test_driver_inputs_endpoint_ok(client):
    """HTTP path shares the app DB (not the in-memory unit-test session)."""
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        existing = db.query(ForecastVersion).filter(ForecastVersion.id == "drv-api-v1").first()
        if existing:
            db.delete(existing)
            db.flush()
        db.add(
            ForecastVersion(
                id="drv-api-v1",
                name="Driver API",
                status="draft",
                version_type="baseline",
                horizon_months=3,
                scenario="base",
            )
        )
        db.commit()
    finally:
        db.close()

    login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    assert login.status_code == 200
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    res = client.get("/api/panel/driver-inputs/drv-api-v1", headers=headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["panel_type"] == "driver_inputs"
    assert "available_line_items" in body["data"]


def test_dashboard_panel_endpoints_smoke(client):
    """Exercise major dashboard routes so dashboard.py stays above the coverage gate."""
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        v = db.query(ForecastVersion).first()
        if not v:
            pytest.skip("No forecast version")
        vid = v.id
    finally:
        db.close()

    login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    assert login.status_code == 200
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    paths = [
        f"/api/panel/review-dashboard/{vid}",
        f"/api/panel/executive-dashboard/{vid}",
        f"/api/panel/accuracy-tracking/{vid}",
        f"/api/panel/anomaly-dashboard/{vid}",
        f"/api/panel/driver-inputs/{vid}",
    ]
    for path in paths:
        res = client.get(path, headers=headers)
        assert res.status_code == 200, f"{path}: {res.text}"
        body = res.json()
        assert "data" in body
        assert body.get("panel_type")
