"""Per-company AnalysisProfile (app/services/analysis_profile.py) and its
admin API (GET/PUT /admin/business-units/{bu_id}/analysis-profile).

Uses the `client`/TestClient(app) fixture pattern (not `db_session`), since
resolve/update go through the real SessionLocal + admin API.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from passlib.context import CryptContext

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")


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


def _make_business_unit(suffix: str) -> str:
    from app.database import SessionLocal
    from app.models.business_unit import BusinessUnit
    from app.models.company import Company

    db = SessionLocal()
    try:
        company = Company(name=f"AnalysisProfileCo-{suffix}")
        db.add(company)
        db.flush()
        bu = BusinessUnit(name=f"AnalysisProfileBU-{suffix}", company_id=company.id)
        db.add(bu)
        db.commit()
        return bu.id
    finally:
        db.close()


def test_resolve_falls_back_to_global_default_when_no_override(client):
    from app.config import settings
    from app.database import SessionLocal
    from app.services.analysis_profile import resolve_analysis_settings

    bu_id = _make_business_unit(uuid.uuid4().hex[:8])
    db = SessionLocal()
    try:
        resolved = resolve_analysis_settings(db, business_unit_id=bu_id)
        assert resolved.materiality_share == settings.materiality_share
        assert resolved.enable_driver_forecasting == settings.enable_driver_forecasting
        assert resolved.min_history_months == settings.min_history_months
    finally:
        db.close()


def test_resolve_uses_override_once_written(client):
    from app.database import SessionLocal
    from app.models.user import Role, User
    from app.services.analysis_profile import resolve_analysis_settings, update_analysis_profile

    bu_id = _make_business_unit(uuid.uuid4().hex[:8])
    db = SessionLocal()
    try:
        admin = db.query(User).filter(User.username == "admin").first()
        update_analysis_profile(
            db,
            business_unit_id=bu_id,
            patch={"min_history_months": 3, "enable_driver_forecasting": True},
            actor=admin,
        )
        db.commit()

        resolved = resolve_analysis_settings(db, business_unit_id=bu_id)
        assert resolved.min_history_months == 3
        assert resolved.enable_driver_forecasting is True

        # A different, untouched business unit stays on the global default.
        other_bu_id = _make_business_unit(uuid.uuid4().hex[:8])
        other_resolved = resolve_analysis_settings(db, business_unit_id=other_bu_id)
        from app.config import settings

        assert other_resolved.min_history_months == settings.min_history_months
        assert other_resolved.enable_driver_forecasting == settings.enable_driver_forecasting
    finally:
        db.close()


def test_update_rejects_unknown_field(client):
    from app.database import SessionLocal
    from app.models.user import User
    from app.services.analysis_profile import update_analysis_profile

    bu_id = _make_business_unit(uuid.uuid4().hex[:8])
    db = SessionLocal()
    try:
        admin = db.query(User).filter(User.username == "admin").first()
        with pytest.raises(ValueError):
            update_analysis_profile(
                db, business_unit_id=bu_id, patch={"not_a_real_field": 1}, actor=admin
            )
    finally:
        db.close()


def test_update_rejects_wrong_type(client):
    from app.database import SessionLocal
    from app.models.user import User
    from app.services.analysis_profile import update_analysis_profile

    bu_id = _make_business_unit(uuid.uuid4().hex[:8])
    db = SessionLocal()
    try:
        admin = db.query(User).filter(User.username == "admin").first()
        with pytest.raises(ValueError):
            update_analysis_profile(
                db, business_unit_id=bu_id, patch={"enable_driver_forecasting": "yes"}, actor=admin
            )
    finally:
        db.close()


def test_admin_api_get_and_put_roundtrip(client):
    bu_id = _make_business_unit(uuid.uuid4().hex[:8])
    headers = _login(client, "admin", "admin")

    r = client.get(f"/api/admin/business-units/{bu_id}/analysis-profile", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["overridden_fields"] == []
    assert "materiality_share" in body["settings"]

    r = client.put(
        f"/api/admin/business-units/{bu_id}/analysis-profile",
        headers=headers,
        json={"patch": {"materiality_share": 0.02, "enable_driver_forecasting": True}},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["settings"]["materiality_share"] == 0.02
    assert body["settings"]["enable_driver_forecasting"] is True
    assert set(body["overridden_fields"]) == {"materiality_share", "enable_driver_forecasting"}

    r = client.get(f"/api/admin/business-units/{bu_id}/analysis-profile", headers=headers)
    assert r.json()["settings"]["materiality_share"] == 0.02


def test_admin_api_put_forbidden_for_non_admin(client):
    bu_id = _make_business_unit(uuid.uuid4().hex[:8])
    headers = _login(client, "analyst", "analyst")

    r = client.put(
        f"/api/admin/business-units/{bu_id}/analysis-profile",
        headers=headers,
        json={"patch": {"materiality_share": 0.02}},
    )
    assert r.status_code == 403


def test_admin_api_unknown_business_unit_404(client):
    headers = _login(client, "admin", "admin")
    r = client.get("/api/admin/business-units/does-not-exist/analysis-profile", headers=headers)
    assert r.status_code == 404


def test_min_history_months_override_changes_pipeline_sparse_classification(client):
    """The wired call path: forecast_pipeline.compute_effective_series honors
    an overridden min_history_months instead of the global default, and a
    second, untouched business unit is unaffected."""
    import pandas as pd
    from app.database import SessionLocal
    from app.models.line_item import LineItem
    from app.models.user import User
    from app.services.analysis_profile import resolve_analysis_settings, update_analysis_profile
    from app.services.forecast_pipeline import compute_effective_series

    overridden_bu = _make_business_unit(uuid.uuid4().hex[:8])
    plain_bu = _make_business_unit(uuid.uuid4().hex[:8])

    db = SessionLocal()
    try:
        admin = db.query(User).filter(User.username == "admin").first()
        # Lower the bar to 5 months so a 10-point series is NOT sparse here,
        # while the global default (12) would still flag it as sparse.
        update_analysis_profile(
            db, business_unit_id=overridden_bu, patch={"min_history_months": 5}, actor=admin
        )
        db.commit()

        li_over = LineItem(
            name="AP Test Line (override)", account_code="AP-1", category="Revenue",
            business_unit_id=overridden_bu,
        )
        li_plain = LineItem(
            name="AP Test Line (plain)", account_code="AP-2", category="Revenue",
            business_unit_id=plain_bu,
        )
        db.add_all([li_over, li_plain])
        db.commit()

        values = pd.Series([100.0 + i for i in range(10)])
        dates = pd.date_range("2024-01-01", periods=10, freq="MS")

        analysis_over = resolve_analysis_settings(db, business_unit_id=overridden_bu)
        result_over = compute_effective_series(li_over, values, dates, analysis_over)
        assert result_over.is_sparse is False

        analysis_plain = resolve_analysis_settings(db, business_unit_id=plain_bu)
        result_plain = compute_effective_series(li_plain, values, dates, analysis_plain)
        assert result_plain.is_sparse is True

        # None (no resolved profile) must behave exactly like the old
        # settings.min_history_months-only code path.
        result_default = compute_effective_series(li_plain, values, dates, None)
        assert result_default.is_sparse is True
    finally:
        db.close()
