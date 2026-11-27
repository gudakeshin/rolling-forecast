"""U2 — driver surfaces, explainability, agent skill reachability."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.domain.registry import get_registry, register_all_skills
from app.domain.skill_loader import load_all_skill_definitions


def test_u2_skill_definitions_present():
    defs = load_all_skill_definitions()
    for name in ("manage_drivers", "explain_variance", "run_what_if"):
        assert name in defs, f"missing skill md: {name}"
        assert defs[name].description
        assert defs[name].parameters


def test_u2_skills_registered():
    register_all_skills()
    names = set(get_registry().list_names())
    assert "manage_drivers" in names
    assert "explain_variance" in names
    assert "run_what_if" in names


def _ensure_drivers_schema() -> None:
    from sqlalchemy import inspect, text

    from app.database import Base, engine

    Base.metadata.create_all(bind=engine)
    insp = inspect(engine)
    with engine.begin() as conn:
        if "roles" in insp.get_table_names():
            role_cols = {c["name"] for c in insp.get_columns("roles")}
            if "can_manage_drivers" not in role_cols:
                conn.execute(
                    text(
                        "ALTER TABLE roles ADD COLUMN can_manage_drivers "
                        "BOOLEAN NOT NULL DEFAULT 0"
                    )
                )
                conn.execute(
                    text(
                        "UPDATE roles SET can_manage_drivers = 1 "
                        "WHERE name IN ('admin', 'analyst', 'reviewer', 'publisher')"
                    )
                )


@pytest.fixture
def client():
    from app.main import app

    _ensure_drivers_schema()
    with TestClient(app) as c:
        yield c


def _auth(client: TestClient, username: str = "analyst") -> dict:
    login = client.post(
        "/api/auth/login",
        json={"username": username, "password": username},
    )
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def test_drivers_list_endpoint(client: TestClient):
    headers = _auth(client, "analyst")
    r = client.get("/api/drivers?include_freshness=true", headers=headers)
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_explainability_bridge_endpoint(client: TestClient):
    headers = _auth(client, "analyst")
    r = client.get(
        "/api/executive/budget-bridge/nonexistent",
        headers=headers,
        params={"attribute": "true"},
    )
    assert r.status_code == 404


def test_what_if_endpoint_validates(client: TestClient):
    headers = _auth(client, "admin")
    r = client.post(
        "/api/scenarios/what-if",
        headers=headers,
        json={
            "base_version_id": "missing",
            "scenario_label": "test",
            "shocks": [{"driver_id": 1, "mode": "pct", "value": -10}],
        },
    )
    assert r.status_code in (400, 404)


def test_what_if_endpoint_e2e(client: TestClient):
    """Full what-if: shock driver → scenario version with perturbed forecast."""
    from app.database import SessionLocal
    from app.models.driver import Driver
    from app.models.forecast import ForecastLineResult, ForecastVersion
    from app.models.line_item import LineItem
    from app.models.user import User
    from app.services.driver_series import assert_link, create_driver, upsert_driver_values

    db = SessionLocal()
    try:
        admin = db.query(User).filter(User.username == "admin").first()
        li = db.query(LineItem).first()
        if li is None:
            li = LineItem(account_code="WH-IF-1", name="What-if Line", category="Revenue", display_order=1)
            db.add(li)
            db.flush()
        base = ForecastVersion(
            name="What-if Base",
            status="draft",
            version_type="baseline",
            horizon_months=2,
            created_by=admin.id,
        )
        db.add(base)
        db.flush()
        db.add_all(
            [
                ForecastLineResult(
                    version_id=base.id,
                    line_item_id=li.id,
                    period="2024-01",
                    p10=90,
                    p50=100.0,
                    p90=110.0,
                    model_p50=100.0,
                ),
                ForecastLineResult(
                    version_id=base.id,
                    line_item_id=li.id,
                    period="2024-02",
                    p10=95,
                    p50=100.0,
                    p90=115.0,
                    model_p50=100.0,
                ),
            ]
        )
        driver = db.query(Driver).filter(Driver.key == "drv_whatif_e2e").first()
        if driver is None:
            driver = create_driver(
                db,
                key="drv_whatif_e2e",
                name="Elasticity Driver",
                driver_type="macro",
                actor=admin,
            )
            db.flush()
        upsert_driver_values(
            db,
            driver_id=driver.id,
            rows=[
                {"period": "2024-01", "value": 100.0},
                {"period": "2024-02", "value": 100.0},
            ],
        )
        assert_link(
            db,
            driver_id=driver.id,
            line_item_id=li.id,
            relation="elasticity",
            coefficient=0.5,
            status="active",
            actor=admin,
        )
        db.commit()
        base_id = base.id
        driver_id = driver.id
    finally:
        db.close()

    headers = _auth(client, "admin")
    r = client.post(
        "/api/scenarios/what-if",
        headers=headers,
        json={
            "base_version_id": base_id,
            "scenario_label": "headcount_down_10",
            "shocks": [{"driver_id": driver_id, "mode": "pct", "value": -10}],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("scenario_version_id")
    assert body.get("affected_line_periods", 0) > 0
