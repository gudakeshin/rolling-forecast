"""Phase 3 — version list endpoints for the frontend versionStore."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.models.forecast import ForecastVersion


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


def _auth_header(client: TestClient, username: str = "admin", password: str = "admin") -> dict[str, str]:
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_list_versions_newest_first(client):
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        older = ForecastVersion(
            id="ver-old",
            name="Older",
            status="draft",
            version_type="baseline",
            horizon_months=3,
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        newer = ForecastVersion(
            id="ver-new",
            name="Newer",
            status="draft",
            version_type="baseline",
            horizon_months=3,
            created_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        )
        # Upsert-style: remove prior test rows if re-run
        for vid in ("ver-old", "ver-new"):
            existing = db.query(ForecastVersion).filter(ForecastVersion.id == vid).first()
            if existing:
                db.delete(existing)
        db.flush()
        db.add_all([older, newer])
        db.commit()
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
        existing = db.query(ForecastVersion).filter(ForecastVersion.id == "ver-detail").first()
        if existing:
            db.delete(existing)
            db.flush()
        v = ForecastVersion(
            id="ver-detail",
            name="Detail Version",
            label="Q2",
            status="draft",
            version_type="baseline",
            horizon_months=6,
            created_at=datetime.now(timezone.utc),
        )
        db.add(v)
        db.commit()
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
