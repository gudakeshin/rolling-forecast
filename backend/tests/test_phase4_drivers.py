"""Phase 4 — causal driver data model, RBAC, materialize, promote."""

from __future__ import annotations

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from passlib.context import CryptContext

from app.models.audit import AuditEvent
from app.models.driver import NO_VERSION_ID, Driver, DriverValue
from app.models.line_item import LineItem
from app.models.user import Role, User
from app.services.driver_series import (
    assert_link,
    create_driver,
    derive_price_from_volume,
    materialize_driver_series,
    promote_link,
    qp_coherence,
    upsert_driver_values,
)
from app.services.permissions import scoped_drivers, user_can_view_driver


# ── Unit / service tests (in-memory db_session) ──────────


def test_create_driver_and_materialize(db_session, seed_users):
    actor = seed_users["analyst"]
    d = create_driver(
        db_session,
        key="units_na",
        name="Units NA",
        driver_type="volume",
        unit="units",
        aggregation="sum",
        business_unit="North America",
        actor=actor,
    )
    db_session.commit()

    n = upsert_driver_values(
        db_session,
        driver_id=d.id,
        rows=[
            {"period": "2024-01", "value": 100.0},
            {"period": "2024-02", "value": 110.0},
            {"period": "2024-03", "value": 120.0},
        ],
        actor=actor,
    )
    db_session.commit()
    assert n == 3

    series = materialize_driver_series(db_session, driver_id=d.id)
    assert list(series.index) == ["2024-01", "2024-02", "2024-03"]
    assert series["2024-02"] == pytest.approx(110.0)

    row = db_session.query(DriverValue).filter(DriverValue.driver_id == d.id).first()
    assert row.version_id == NO_VERSION_ID

    audits = (
        db_session.query(AuditEvent)
        .filter(AuditEvent.action.in_(["driver.create", "driver.values.ingest"]))
        .all()
    )
    assert {a.action for a in audits} >= {"driver.create", "driver.values.ingest"}


def test_upsert_idempotent_on_conflict(db_session, seed_users):
    actor = seed_users["admin"]
    d = create_driver(
        db_session,
        key="hc_emea",
        name="Headcount EMEA",
        driver_type="headcount",
        aggregation="end_of_period",
        actor=actor,
    )
    db_session.commit()
    upsert_driver_values(
        db_session,
        driver_id=d.id,
        rows=[{"period": "2024-01", "value": 50.0}],
        actor=actor,
    )
    db_session.commit()
    upsert_driver_values(
        db_session,
        driver_id=d.id,
        rows=[{"period": "2024-01", "value": 55.0}],
        actor=actor,
    )
    db_session.commit()
    series = materialize_driver_series(db_session, driver_id=d.id)
    assert len(series) == 1
    assert series["2024-01"] == pytest.approx(55.0)


def test_bu_scope_hides_other_bu_drivers(db_session, seed_users):
    admin = seed_users["admin"]
    create_driver(
        db_session,
        key="units_emea",
        name="Units EMEA",
        driver_type="volume",
        business_unit="EMEA",
        actor=admin,
    )
    create_driver(
        db_session,
        key="units_shared",
        name="Units Shared",
        driver_type="volume",
        business_unit=None,
        actor=admin,
    )
    db_session.commit()

    analyst = seed_users["analyst"]  # North America
    visible = {d.key for d in scoped_drivers(db_session, analyst).all()}
    assert "units_shared" in visible
    assert "units_emea" not in visible

    emea = db_session.query(Driver).filter(Driver.key == "units_emea").first()
    assert user_can_view_driver(analyst, emea) is False
    assert user_can_view_driver(admin, emea) is True


def test_promote_link_audits(db_session, seed_users, seed_line_items):
    actor = seed_users["analyst"]
    line = seed_line_items["REV-001"]

    d = create_driver(
        db_session,
        key="asp_units",
        name="Units",
        driver_type="volume",
        business_unit=line.business_unit,
        actor=actor,
    )
    db_session.flush()
    link = assert_link(
        db_session,
        driver_id=d.id,
        line_item_id=line.id,
        relation="quantity",
        composition_group="rev_qp",
        status="candidate",
        actor=actor,
    )
    db_session.commit()

    promote_link(db_session, link=link, actor=actor)
    db_session.commit()
    assert link.status == "active"

    evt = (
        db_session.query(AuditEvent)
        .filter(AuditEvent.action == "driver.link.promote")
        .order_by(AuditEvent.id.desc())
        .first()
    )
    assert evt is not None
    assert evt.details.get("relation") == "quantity"
    assert evt.details.get("from_status") == "candidate"


def test_qp_coherence_and_derived_price():
    q = pd.Series({"2024-01": 10.0, "2024-02": 20.0})
    p = pd.Series({"2024-01": 5.0, "2024-02": 5.0})
    line = pd.Series({"2024-01": 50.0, "2024-02": 100.0})
    check = qp_coherence(q, p, line)
    assert check["coherent"] is True
    assert check["max_rel_error"] == pytest.approx(0.0)

    bad = qp_coherence(q, p, pd.Series({"2024-01": 50.0, "2024-02": 80.0}))
    assert bad["coherent"] is False

    price, meta = derive_price_from_volume(line, q)
    assert price["2024-01"] == pytest.approx(5.0)
    assert meta["price_source"] == "derived_l_over_q"


def test_invalid_driver_type_rejected(db_session, seed_users):
    with pytest.raises(ValueError, match="driver_type"):
        create_driver(
            db_session,
            key="bad",
            name="Bad",
            driver_type="not_a_type",
            actor=seed_users["admin"],
        )


# ── API tests (app TestClient DB) ────────────────────────


def _ensure_phase4_schema() -> None:
    """Patch persistent test_app.db when create_all won't add new columns."""
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

    _ensure_phase4_schema()
    with TestClient(app) as c:
        yield c


def _auth(client: TestClient, username: str = "analyst", password: str | None = None) -> dict:
    pwd = password or username
    login = client.post("/api/auth/login", json={"username": username, "password": pwd})
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def test_api_create_ingest_promote(client: TestClient):
    headers = _auth(client, "analyst")

    # Ensure a line item exists in the app DB
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        line = db.query(LineItem).first()
        if line is None:
            line = LineItem(
                account_code="REV-P4",
                name="P4 Revenue",
                category="Revenue",
                display_order=1,
            )
            db.add(line)
            db.commit()
            db.refresh(line)
        line_id = line.id
    finally:
        db.close()

    key = "api_units_p4"
    # Clean prior run
    r = client.get("/api/drivers", headers=headers)
    assert r.status_code == 200
    for d in r.json():
        if d["key"] == key:
            break

    r = client.post(
        "/api/drivers",
        headers=headers,
        json={
            "key": key,
            "name": "API Units",
            "driver_type": "volume",
            "business_unit": "North America",
        },
    )
    if r.status_code == 409:
        # Already exists from prior run — find it
        drivers = client.get("/api/drivers", headers=headers).json()
        driver_id = next(d["id"] for d in drivers if d["key"] == key)
    else:
        assert r.status_code == 201, r.text
        driver_id = r.json()["id"]

    r = client.post(
        f"/api/drivers/{driver_id}/values",
        headers=headers,
        json={"rows": [{"period": "2024-01", "value": 42.0}]},
    )
    assert r.status_code == 200, r.text

    r = client.post(
        "/api/drivers/links",
        headers=headers,
        json={
            "driver_id": driver_id,
            "line_item_id": line_id,
            "relation": "quantity",
            "status": "candidate",
            "composition_group": "rev_qp",
        },
    )
    assert r.status_code == 201, r.text
    link_id = r.json()["id"]

    r = client.post(f"/api/drivers/links/{link_id}/promote", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "active"

    r = client.get(f"/api/drivers/by-line/{line_id}/links", headers=headers)
    assert r.status_code == 200
    assert any(l["id"] == link_id for l in r.json())


def test_manage_drivers_permission_required(client: TestClient):
    from app.database import SessionLocal

    pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
    db = SessionLocal()
    try:
        role = db.query(Role).filter(Role.name == "input_provider").first()
        if role is None:
            role = Role(
                name="input_provider",
                description="BU",
                can_input=True,
                can_manage_drivers=False,
            )
            db.add(role)
            db.flush()
        else:
            role.can_manage_drivers = False
        user = db.query(User).filter(User.username == "input_only_p4").first()
        if user is None:
            db.add(
                User(
                    email="input_p4@test.local",
                    username="input_only_p4",
                    hashed_password=pwd.hash("input_only_p4"),
                    full_name="Input",
                    business_unit="North America",
                    role_id=role.id,
                )
            )
        db.commit()
    finally:
        db.close()

    login = client.post(
        "/api/auth/login",
        json={"username": "input_only_p4", "password": "input_only_p4"},
    )
    assert login.status_code == 200, login.text
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    r = client.get("/api/drivers", headers=headers)
    assert r.status_code == 403
