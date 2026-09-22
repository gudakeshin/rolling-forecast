"""Phase 5 — JWT refresh/revocation + scenario filter."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from jose import jwt

from app.config import settings
from app.models.forecast import ForecastVersion
from app.services.token_store import (
    create_access_token,
    is_jti_revoked,
    revoke_jti,
)


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
        storage = getattr(limiter, "_storage", None)
        if storage is not None and hasattr(storage, "reset"):
            storage.reset()

    with TestClient(app) as c:
        yield c


def test_login_returns_refresh_and_access_has_jti(client):
    r = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("access_token")
    assert body.get("refresh_token")
    payload = jwt.decode(
        body["access_token"],
        settings.jwt_secret_key,
        algorithms=[settings.jwt_algorithm],
    )
    assert payload.get("jti")
    assert payload.get("sub")


def test_refresh_rotation_and_reuse_rejected(client):
    login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    assert login.status_code == 200
    refresh1 = login.json()["refresh_token"]

    r1 = client.post("/api/auth/refresh", json={"refresh_token": refresh1})
    assert r1.status_code == 200, r1.text
    refresh2 = r1.json()["refresh_token"]
    assert refresh2 != refresh1
    assert r1.json()["access_token"]

    # Old refresh must be rejected (reuse)
    reuse = client.post("/api/auth/refresh", json={"refresh_token": refresh1})
    assert reuse.status_code == 401


def test_logout_revokes_access_token(client):
    login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    access = login.json()["access_token"]
    refresh = login.json()["refresh_token"]
    headers = {"Authorization": f"Bearer {access}"}

    me = client.get("/api/auth/me", headers=headers)
    assert me.status_code == 200

    out = client.post(
        "/api/auth/logout",
        headers=headers,
        json={"refresh_token": refresh},
    )
    assert out.status_code == 200

    me2 = client.get("/api/auth/me", headers=headers)
    assert me2.status_code == 401

    # Rotated refresh family also revoked
    bad = client.post("/api/auth/refresh", json={"refresh_token": refresh})
    assert bad.status_code == 401


def test_denylist_db_fallback(db_session):
    token, jti, exp = create_access_token({"sub": "u1", "role": "admin"})
    assert token
    assert not is_jti_revoked(db_session, jti)
    revoke_jti(db_session, jti, exp)
    assert is_jti_revoked(db_session, jti)


def test_versions_scenario_filter(client):
    from sqlalchemy import inspect, text

    from app.database import SessionLocal, engine

    # Ensure Phase 5 column exists on the persistent test_app.db (create_all won't ALTER)
    cols = {c["name"] for c in inspect(engine).get_columns("forecast_versions")}
    if "scenario" not in cols:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "ALTER TABLE forecast_versions "
                    "ADD COLUMN scenario VARCHAR(64) DEFAULT 'base' NOT NULL"
                )
            )
    # Auth token tables for login/refresh may also be missing on old test DBs
    tables = set(inspect(engine).get_table_names())
    if "refresh_tokens" not in tables or "token_denylist" not in tables:
        from app.database import Base
        import app.models  # noqa: F401

        Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        for vid, scen in (("sc-base", "base"), ("sc-up", "upside")):
            existing = db.query(ForecastVersion).filter(ForecastVersion.id == vid).first()
            if existing:
                db.delete(existing)
            db.add(
                ForecastVersion(
                    id=vid,
                    name=f"V-{scen}",
                    status="draft",
                    version_type="scheduled",
                    scenario=scen,
                    horizon_months=3,
                    created_at=datetime.now(timezone.utc),
                )
            )
        db.commit()
    finally:
        db.close()

    login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    all_v = client.get("/api/panel/versions", headers=headers)
    assert all_v.status_code == 200
    ids = {v["id"] for v in all_v.json()}
    assert "sc-base" in ids and "sc-up" in ids

    up = client.get("/api/panel/versions?scenario=upside", headers=headers)
    assert up.status_code == 200
    up_ids = {v["id"] for v in up.json()}
    assert "sc-up" in up_ids
    assert "sc-base" not in up_ids
