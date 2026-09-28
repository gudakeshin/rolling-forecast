"""Company scoping for LearnedHeuristic (app/services/reflection.py,
app/api/heuristics.py) -- see migration 029_heuristic_and_memory_scoping.

Two kinds of tests:
- `run_reflection_pass`'s own business_unit_id scoping, using the
  `db_session`/`seed_*` fixture pattern (see tests/test_phase_m3_reflection.py
  for the exact accuracy-record shape this mirrors).
- The REST layer (`GET /heuristics`, promote, reject), using the
  `client`/TestClient(app) pattern (see tests/test_tenant_scope.py), since
  only that path exercises the real permission dependencies.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from passlib.context import CryptContext

from app.models.forecast import ForecastVersion
from app.models.fx import ForecastAccuracyRecord
from app.models.heuristic import LearnedHeuristic
from app.models.line_item import LineItem
from app.services.reflection import run_reflection_pass

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")


def _accuracy_records(db_session, *, line_item_id: int, created_by: str, bias_pct: float = 12.0):
    for i in range(3):
        version = ForecastVersion(
            name=f"FC-HS-{line_item_id}-{i + 1:02d}",
            base_period=f"2025-{i + 1:02d}",
            created_by=created_by,
        )
        db_session.add(version)
        db_session.flush()
        db_session.add(
            ForecastAccuracyRecord(
                version_id=version.id,
                line_item_id=line_item_id,
                period=f"2025-{i + 4:02d}",
                horizon_offset=3,
                predicted_p50=100.0 * (1 + bias_pct / 100.0),
                actual=100.0,
                absolute_error=100.0 * bias_pct / 100.0,
                pct_error=bias_pct,
                model_type="sarimax",
            )
        )
    db_session.commit()


def test_run_reflection_pass_is_scoped_by_business_unit(db_session, seed_users, seed_line_items):
    """Bias data for a SECOND company must not produce (or be deduped
    against) a candidate for the first company's identically-shaped data."""
    from app.models.business_unit import BusinessUnit

    analyst = seed_users["analyst"]
    bu_a = seed_line_items["REV-001"].business_unit_id
    revenue_a = seed_line_items["REV-001"]

    bu_b = BusinessUnit(name=f"HeuristicScopeCo-{uuid.uuid4().hex[:8]}")
    db_session.add(bu_b)
    db_session.flush()
    revenue_b = LineItem(
        name="Other Co Revenue", account_code="HS-REV-B", category="Revenue",
        business_unit_id=bu_b.id,
    )
    db_session.add(revenue_b)
    db_session.flush()

    _accuracy_records(db_session, line_item_id=revenue_a.id, created_by=analyst.id)
    _accuracy_records(db_session, line_item_id=revenue_b.id, created_by=analyst.id)

    summary_a = run_reflection_pass(db_session, actor=analyst, business_unit_id=bu_a)
    assert summary_a["created"] == 1

    rows = db_session.query(LearnedHeuristic).all()
    assert len(rows) == 1
    assert rows[0].line_item_id == revenue_a.id
    assert rows[0].business_unit_id == bu_a

    # Company B's identical bias is untouched -- a second, BU-B-scoped pass
    # must still create its own row rather than treating A's as a duplicate.
    summary_b = run_reflection_pass(db_session, actor=analyst, business_unit_id=bu_b.id)
    assert summary_b["created"] == 1
    rows = db_session.query(LearnedHeuristic).order_by(LearnedHeuristic.id).all()
    assert len(rows) == 2
    assert {r.business_unit_id for r in rows} == {bu_a, bu_b.id}


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


def _login(client: TestClient, username: str, password: str) -> dict[str, str]:
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _make_reviewer(suffix: str) -> dict[str, str]:
    """A company-scoped user with review permission (not cross-BU) -- the
    role the demo seed doesn't ship, built the same way
    tests/test_actionable_reviews.py builds one-off roles."""
    from app.database import SessionLocal
    from app.models.business_unit import BusinessUnit
    from app.models.company import Company
    from app.models.user import Role, User

    suffix = f"{suffix}-{uuid.uuid4().hex[:8]}"
    db = SessionLocal()
    try:
        company = Company(name=f"HeuristicRestCo-{suffix}")
        db.add(company)
        db.flush()
        bu = BusinessUnit(name=f"HeuristicRestBU-{suffix}", company_id=company.id)
        db.add(bu)
        db.flush()

        role = Role(
            name=f"hs_reviewer_{suffix}",
            description="Scoped reviewer (test-only)",
            can_input=True, can_generate=True, can_override=False,
            can_review=True, can_publish=False, can_admin=False,
            can_view_all_bus=False,
        )
        db.add(role)
        db.flush()

        username = f"hs_reviewer_{suffix}"
        user = User(
            email=f"{username}@test.local",
            username=username,
            hashed_password=_pwd.hash(username),
            full_name="Scoped Reviewer",
            business_unit_id=bu.id,
            role_id=role.id,
        )
        db.add(user)

        line_item = LineItem(
            name=f"HS Revenue {suffix}", account_code=f"HS-{suffix}", category="Revenue",
            business_unit_id=bu.id,
        )
        db.add(line_item)
        db.commit()
        return {
            "username": username, "password": username,
            "business_unit_id": bu.id, "line_item_id": line_item.id,
        }
    finally:
        db.close()


def _seed_heuristic(business_unit_id: str, line_item_id: int) -> int:
    from app.database import SessionLocal
    from datetime import datetime, timedelta, timezone

    db = SessionLocal()
    try:
        row = LearnedHeuristic(
            scope="line_item",
            line_item_id=line_item_id,
            business_unit_id=business_unit_id,
            model_type="sarimax",
            horizon_bucket="1-3",
            kind="error_bias",
            statement="Test heuristic for REST scoping",
            effect_size=5.0,
            evidence={"cycles": 3},
            status="candidate",
            source="actuals",
            proposed_at=datetime.now(timezone.utc),
            review_by=datetime.now(timezone.utc) + timedelta(days=90),
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row.id
    finally:
        db.close()


def test_list_heuristics_is_scoped_by_business_unit(client):
    co_a = _make_reviewer("list-a")
    co_b = _make_reviewer("list-b")
    _seed_heuristic(co_a["business_unit_id"], co_a["line_item_id"])

    headers_a = _login(client, co_a["username"], co_a["password"])
    r = client.get("/api/heuristics", headers=headers_a)
    assert r.status_code == 200
    assert r.json()["count"] == 1

    headers_b = _login(client, co_b["username"], co_b["password"])
    r = client.get("/api/heuristics", headers=headers_b)
    assert r.status_code == 200
    assert r.json()["count"] == 0


def test_promote_and_reject_are_scoped_by_business_unit(client):
    co_a = _make_reviewer("act-a")
    co_b = _make_reviewer("act-b")
    heuristic_id = _seed_heuristic(co_a["business_unit_id"], co_a["line_item_id"])

    headers_b = _login(client, co_b["username"], co_b["password"])
    r = client.post(f"/api/heuristics/{heuristic_id}/promote", headers=headers_b)
    assert r.status_code == 404

    r = client.post(f"/api/heuristics/{heuristic_id}/reject", headers=headers_b)
    assert r.status_code == 404

    headers_a = _login(client, co_a["username"], co_a["password"])
    r = client.post(f"/api/heuristics/{heuristic_id}/promote", headers=headers_a)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "active"
