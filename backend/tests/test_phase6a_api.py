"""Phase 6a API — budget bridge attribution + driver drilldown (Stop B)."""

from __future__ import annotations

import asyncio
import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event

from app.models.forecast import ForecastLineResult, ForecastVersion
from app.models.line_item import LineItem
from app.models.override import Override
from app.models.user import User
from app.services.variance_attribution import BridgeAttributionContext, attribute_bridge_row


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


def _auth(client: TestClient, username: str = "admin", password: str = "admin") -> dict[str, str]:
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_budget_bridge_attribute_returns_explained_pct(client):
    """?attribute=true attaches honest attribution with explained_pct + residual bucket."""
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        admin = db.query(User).filter(User.username == "admin").first()
        assert admin is not None
        li = db.query(LineItem).filter(LineItem.account_code == "REV-API-1").first()
        if li is None:
            li = db.query(LineItem).filter(LineItem.account_code == "REV-001").first()
        if li is None:
            li = LineItem(
                account_code="REV-API-1",
                name="API Revenue",
                category="Revenue",
                display_order=1,
            )
            db.add(li)
            db.flush()

        version = ForecastVersion(
            name="Bridge Attr API",
            status="draft",
            version_type="baseline",
            horizon_months=3,
            created_by=admin.id,
        )
        db.add(version)
        db.flush()
        db.add(
            ForecastLineResult(
                version_id=version.id,
                line_item_id=li.id,
                period="2024-06",
                p10=900.0,
                p50=1200.0,
                p90=1300.0,
                model_p50=1000.0,
                model_type="linear",
                confidence_score=70.0,
                confidence_level="medium",
            )
        )
        db.add(
            Override(
                version_id=version.id,
                line_item_id=li.id,
                period="2024-06",
                original_model_value=1000.0,
                override_value=1200.0,
                reason="manual uplift for pipeline strength",
                user_id=admin.id,
                status="active",
            )
        )
        db.commit()
        version_id = version.id
        li_id = li.id
    finally:
        db.close()

    headers = _auth(client)
    r = client.get(
        f"/api/executive/budget-bridge/{version_id}?attribute=true&page_size=50&materiality_pct=0",
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("attribute") is True
    assert body.get("convention") == "volume_first"

    row = next((x for x in body["rows"] if x["line_item_id"] == li_id), None)
    assert row is not None
    attr = row.get("attribution")
    assert attr is not None
    assert "explained_pct" in attr
    assert "unattributed" in attr.get("buckets", {})
    assert "volume" not in attr.get("buckets", {})
    assert attr.get("method") == "override_reason_text"


def test_driver_drilldown_no_keyword_buckets(client):
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        admin = db.query(User).filter(User.username == "admin").first()
        assert admin is not None
        li = db.query(LineItem).filter(LineItem.account_code == "REV-API-2").first()
        if li is None:
            li = db.query(LineItem).filter(LineItem.account_code == "REV-001").first()
        if li is None:
            li = LineItem(
                account_code="REV-API-2",
                name="API Revenue 2",
                category="Revenue",
                display_order=2,
            )
            db.add(li)
            db.flush()
        version = ForecastVersion(
            name="Drill Attr API",
            status="draft",
            version_type="baseline",
            horizon_months=3,
            created_by=admin.id,
        )
        db.add(version)
        db.flush()
        db.add(
            Override(
                version_id=version.id,
                line_item_id=li.id,
                period="2024-07",
                original_model_value=500.0,
                override_value=450.0,
                reason="price compression and fx headwind",
                user_id=admin.id,
                status="active",
            )
        )
        db.commit()
        version_id = version.id
        li_id = li.id
    finally:
        db.close()

    headers = _auth(client)
    r = client.get(
        f"/api/executive/driver-drilldown/{version_id}?line_item_id={li_id}",
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("override_breakdown") == []
    assert "keyword" in body.get("note", "").lower()
    attrs = body.get("attributions") or []
    assert len(attrs) >= 1
    buckets = attrs[0].get("buckets") or {}
    assert attrs[0].get("method") == "override_reason_text"
    assert set(buckets.keys()) == {"unattributed"}


def test_bridge_attribution_context_batches_queries(db_engine, db_session, seed_users, seed_line_items):
    """attribute_bridge_row with shared context should not re-query overrides per line."""
    items = [li for li in seed_line_items.values() if not li.is_calculated][:3]
    if not items:
        pytest.skip("No line items")
    version = ForecastVersion(
        id="bridge-ctx-v1",
        name="Ctx",
        status="draft",
        version_type="baseline",
        horizon_months=2,
        created_by=seed_users["admin"].id,
    )
    db_session.add(version)
    db_session.flush()
    for li in items:
        db_session.add(
            ForecastLineResult(
                version_id=version.id,
                line_item_id=li.id,
                period="2024-01",
                p10=90.0,
                p50=100.0,
                p90=110.0,
                model_type="linear",
                confidence_score=70.0,
                confidence_level="medium",
            )
        )
        db_session.add(
            Override(
                version_id=version.id,
                line_item_id=li.id,
                period="2024-01",
                original_model_value=100.0,
                override_value=110.0,
                reason="test override",
                user_id=seed_users["analyst"].id,
                status="active",
            )
        )
    db_session.commit()

    ctx = BridgeAttributionContext(db_session, version.id)
    target_ids = [li.id for li in items]

    state = {"n": 0}

    def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        state["n"] += 1

    event.listen(db_engine, "before_cursor_execute", before_cursor_execute)
    try:
        state["n"] = 0
        for lid in target_ids:
            attribute_bridge_row(
                db_session,
                line_item_id=lid,
                version_id=version.id,
                variance_vs_prior=10.0,
                ctx=ctx,
            )
        with_ctx = state["n"]

        state["n"] = 0
        for lid in target_ids:
            attribute_bridge_row(
                db_session,
                line_item_id=lid,
                version_id=version.id,
                variance_vs_prior=10.0,
                ctx=None,
            )
        without_ctx = state["n"]
    finally:
        event.remove(db_engine, "before_cursor_execute", before_cursor_execute)

    assert with_ctx < without_ctx
    assert with_ctx <= 2 + len(target_ids)


def test_budget_bridge_handler_batches_attribution(db_session, seed_users, seed_line_items):
    """budget_bridge with attribute=true should not issue per-line override queries."""
    from app.api.executive import budget_bridge

    li_ids = [li.id for li in seed_line_items.values() if not li.is_calculated][:4]
    version = ForecastVersion(
        id="bridge-api-batch",
        name="Batch Bridge",
        status="draft",
        version_type="baseline",
        horizon_months=3,
        created_by=seed_users["admin"].id,
    )
    db_session.add(version)
    db_session.flush()
    for lid in li_ids:
        db_session.add(
            ForecastLineResult(
                version_id=version.id,
                line_item_id=lid,
                period="2024-03",
                p10=90.0,
                p50=100.0,
                p90=110.0,
                model_type="linear",
                confidence_score=70.0,
                confidence_level="medium",
            )
        )
    db_session.commit()

    admin = seed_users["admin"]
    engine = db_session.get_bind()
    state = {"n": 0}

    def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        state["n"] += 1

    event.listen(engine, "before_cursor_execute", before_cursor_execute)
    try:
        state["n"] = 0
        result = asyncio.run(
            budget_bridge(
                version_id=version.id,
                budget_version_id=None,
                materiality_pct=0.0,
                page=1,
                page_size=10,
                attribute=True,
                convention="volume_first",
                current_user=admin,
                db=db_session,
            )
        )
        queries = state["n"]
    finally:
        event.remove(engine, "before_cursor_execute", before_cursor_execute)

    assert result["attribute"] is True
    assert len(result["rows"]) <= 10
    assert queries < 20 + len(li_ids) * 2
