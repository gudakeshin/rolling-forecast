"""Phase 3 — version list, scenario filter, and shared-version API contracts for UX."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text

from app.models.forecast import ForecastVersion


def _ensure_scenario_column() -> None:
    """Persistent test_app.db may predate migration 012; create_all won't ALTER."""
    from app.database import engine

    cols = {c["name"] for c in inspect(engine).get_columns("forecast_versions")}
    if "scenario" not in cols:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "ALTER TABLE forecast_versions "
                    "ADD COLUMN scenario VARCHAR(64) DEFAULT 'base' NOT NULL"
                )
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

    _ensure_scenario_column()
    with TestClient(app) as c:
        yield c


def _auth_header(client: TestClient, username: str = "admin", password: str = "admin") -> dict[str, str]:
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _upsert_versions(db, rows: list[ForecastVersion]) -> None:
    for v in rows:
        existing = db.query(ForecastVersion).filter(ForecastVersion.id == v.id).first()
        if existing:
            db.delete(existing)
    db.flush()
    db.add_all(rows)
    db.commit()


def test_list_versions_newest_first(client):
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        _upsert_versions(
            db,
            [
                ForecastVersion(
                    id="ver-old",
                    name="Older",
                    status="draft",
                    version_type="baseline",
                    horizon_months=3,
                    scenario="base",
                    created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                ),
                ForecastVersion(
                    id="ver-new",
                    name="Newer",
                    status="draft",
                    version_type="baseline",
                    horizon_months=3,
                    scenario="base",
                    created_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
                ),
            ],
        )
    finally:
        db.close()

    headers = _auth_header(client)
    res = client.get("/api/panel/versions", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert isinstance(data, list)
    ids = [v["id"] for v in data]
    assert "ver-new" in ids and "ver-old" in ids
    assert ids.index("ver-new") < ids.index("ver-old")


def test_get_version_detail(client):
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        _upsert_versions(
            db,
            [
                ForecastVersion(
                    id="ver-detail",
                    name="Detail Version",
                    label="Q2",
                    status="draft",
                    version_type="baseline",
                    horizon_months=6,
                    scenario="base",
                    created_at=datetime.now(timezone.utc),
                ),
            ],
        )
    finally:
        db.close()

    headers = _auth_header(client)
    res = client.get("/api/panel/version/ver-detail", headers=headers)
    assert res.status_code == 200
    body = res.json()
    assert body["id"] == "ver-detail"
    assert body["name"] == "Detail Version"
    assert body["label"] == "Q2"

    missing = client.get("/api/panel/version/nope", headers=headers)
    assert missing.status_code == 404


def test_list_versions_includes_scenario_and_filters(client):
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        _upsert_versions(
            db,
            [
                ForecastVersion(
                    id="ver-base-ux",
                    name="Base UX",
                    status="draft",
                    version_type="baseline",
                    horizon_months=3,
                    scenario="base",
                    created_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
                ),
                ForecastVersion(
                    id="ver-up-ux",
                    name="Upside UX",
                    status="draft",
                    version_type="baseline",
                    horizon_months=3,
                    scenario="upside",
                    created_at=datetime(2026, 3, 2, tzinfo=timezone.utc),
                ),
            ],
        )
    finally:
        db.close()

    headers = _auth_header(client)
    all_res = client.get("/api/panel/versions", headers=headers)
    assert all_res.status_code == 200
    by_id = {v["id"]: v for v in all_res.json()}
    assert by_id["ver-base-ux"].get("scenario", "base") == "base"
    assert by_id["ver-up-ux"]["scenario"] == "upside"

    filtered = client.get("/api/panel/versions?scenario=upside", headers=headers)
    assert filtered.status_code == 200
    filtered_ids = {v["id"] for v in filtered.json()}
    assert "ver-up-ux" in filtered_ids
    assert "ver-base-ux" not in filtered_ids


def test_review_and_overrides_panels_share_version_param(client):
    """Both analyst panels resolve the same version_id (shared active-version UX)."""
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        v = db.query(ForecastVersion).first()
        if not v:
            pytest.skip("No forecast version")
        vid = v.id
    finally:
        db.close()

    headers = _auth_header(client)
    review = client.get(f"/api/panel/review-dashboard/{vid}", headers=headers)
    overrides = client.get(f"/api/panel/overrides/{vid}", headers=headers)
    assert review.status_code == 200, review.text
    assert overrides.status_code == 200, overrides.text
    assert review.json()["data"]["version"]["id"] == vid
    assert overrides.json()["data"]["version"]["id"] == vid
    assert review.json()["data"]["version"]["name"] == overrides.json()["data"]["version"]["name"]


def test_inline_override_error_surfaces_message(client):
    """Failed mutations return a clear detail string for toast wiring."""
    headers = _auth_header(client)
    res = client.post(
        "/api/panel/inline-override",
        headers=headers,
        json={
            "result_id": "does-not-exist",
            "new_value": 1.0,
            "reason": "short",
            "apply_to": "single",
        },
    )
    assert res.status_code in (400, 403, 404, 422)
    body = res.json()
    assert "detail" in body


def test_accuracy_and_driver_panels_share_version_param(client):
    """Accuracy + driver-input panels resolve the same active version (shared UX)."""
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        v = db.query(ForecastVersion).first()
        if not v:
            pytest.skip("No forecast version")
        vid = v.id
        vname = v.name
    finally:
        db.close()

    headers = _auth_header(client)
    accuracy = client.get(f"/api/panel/accuracy-tracking/{vid}", headers=headers)
    drivers = client.get(f"/api/panel/driver-inputs/{vid}", headers=headers)
    assert accuracy.status_code == 200, accuracy.text
    assert drivers.status_code == 200, drivers.text
    assert accuracy.json()["data"]["version"]["id"] == vid
    assert drivers.json()["data"]["version"]["id"] == vid
    assert accuracy.json()["data"]["version"]["name"] == vname
    assert drivers.json()["data"]["version"]["name"] == vname


def test_approvals_status_error_surfaces_detail(client):
    """Missing version for approvals returns a toast-ready detail message."""
    headers = _auth_header(client)
    res = client.get("/api/approvals/status/does-not-exist", headers=headers)
    assert res.status_code in (404, 422)
    assert "detail" in res.json()


def test_rescore_error_surfaces_detail(client):
    """Rescore on a missing version returns a clear detail for toast wiring."""
    headers = _auth_header(client)
    res = client.post("/api/panel/rescore-forecasts/does-not-exist", headers=headers)
    assert res.status_code in (404, 403, 422)
    assert "detail" in res.json()
