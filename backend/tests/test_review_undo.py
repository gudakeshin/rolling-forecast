"""Phase 4.4 — the review undo toast is backed by a real reversal endpoint."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from passlib.context import CryptContext

from app.models.forecast import ForecastVersion, ForecastLineResult
from app.models.line_item import LineItem
from app.models.review_undo import ReviewUndoSnapshot
from app.models.user import Role, User


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


def _auth_header(client: TestClient, username: str, password: str) -> dict[str, str]:
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _seed_two_line_items(db, *, prefix: str):
    admin = db.query(User).filter(User.username == "admin").first()
    unique = uuid.uuid4().hex[:8]
    li_a = LineItem(name=f"{prefix} Rev A", account_code=f"{prefix}-{unique}-1", category="Revenue")
    li_b = LineItem(name=f"{prefix} Rev B", account_code=f"{prefix}-{unique}-2", category="Revenue")
    db.add_all([li_a, li_b])
    db.flush()

    version = ForecastVersion(
        name=f"FC-Undo-{prefix}",
        status="in_review",
        version_type="scheduled",
        horizon_months=1,
        created_by=admin.id,
        created_at=datetime.now(timezone.utc),
    )
    db.add(version)
    db.flush()

    row_a = ForecastLineResult(version_id=version.id, line_item_id=li_a.id, period="2025-01", p50=100.0)
    row_b = ForecastLineResult(version_id=version.id, line_item_id=li_b.id, period="2025-01", p50=200.0)
    db.add_all([row_a, row_b])
    db.commit()
    return version.id, row_a.id, row_b.id


def test_batch_review_returns_undo_token_and_undo_restores_state(client):
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        version_id, row_a_id, row_b_id = _seed_two_line_items(db, prefix="Batch")
    finally:
        db.close()

    headers = _auth_header(client, "admin", "admin")

    r = client.post(
        "/api/panel/batch-review",
        json={"version_id": version_id, "item_ids": [row_a_id, row_b_id], "action": "approve"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True
    undo_token = body["undo_token"]
    assert undo_token

    db = SessionLocal()
    try:
        row_a = db.query(ForecastLineResult).filter(ForecastLineResult.id == row_a_id).one()
        row_b = db.query(ForecastLineResult).filter(ForecastLineResult.id == row_b_id).one()
        assert row_a.review_status == "approve"
        assert row_b.review_status == "approve"
        assert row_a.reviewed_by is not None
    finally:
        db.close()

    r = client.post("/api/panel/undo-review", json={"undo_token": undo_token}, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["restored_count"] == 2

    db = SessionLocal()
    try:
        row_a = db.query(ForecastLineResult).filter(ForecastLineResult.id == row_a_id).one()
        row_b = db.query(ForecastLineResult).filter(ForecastLineResult.id == row_b_id).one()
        assert row_a.review_status is None
        assert row_b.review_status is None
        assert row_a.reviewed_by is None
        assert row_a.reviewed_at is None
    finally:
        db.close()


def test_undo_review_token_is_one_shot(client):
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        version_id, row_a_id, row_b_id = _seed_two_line_items(db, prefix="OneShot")
    finally:
        db.close()

    headers = _auth_header(client, "admin", "admin")
    r = client.post(
        "/api/panel/batch-review",
        json={"version_id": version_id, "item_ids": [row_a_id], "action": "approve"},
        headers=headers,
    )
    undo_token = r.json()["undo_token"]

    r1 = client.post("/api/panel/undo-review", json={"undo_token": undo_token}, headers=headers)
    assert r1.status_code == 200

    r2 = client.post("/api/panel/undo-review", json={"undo_token": undo_token}, headers=headers)
    assert r2.status_code == 409


def test_undo_review_expired_token_returns_410(client):
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        version_id, row_a_id, row_b_id = _seed_two_line_items(db, prefix="Expired")
    finally:
        db.close()

    headers = _auth_header(client, "admin", "admin")
    r = client.post(
        "/api/panel/batch-review",
        json={"version_id": version_id, "item_ids": [row_a_id], "action": "approve"},
        headers=headers,
    )
    undo_token = r.json()["undo_token"]

    db = SessionLocal()
    try:
        snap = db.query(ReviewUndoSnapshot).filter(ReviewUndoSnapshot.id == undo_token).one()
        snap.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
    finally:
        db.close()

    r2 = client.post("/api/panel/undo-review", json={"undo_token": undo_token}, headers=headers)
    assert r2.status_code == 410


def test_undo_review_unknown_token_404(client):
    headers = _auth_header(client, "admin", "admin")
    r = client.post("/api/panel/undo-review", json={"undo_token": "does-not-exist"}, headers=headers)
    assert r.status_code == 404


def test_undo_review_wrong_user_403(client):
    from app.database import SessionLocal

    pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
    db = SessionLocal()
    try:
        version_id, row_a_id, row_b_id = _seed_two_line_items(db, prefix="Owner")

        role = db.query(Role).filter(Role.name == "admin").first()
        other = db.query(User).filter(User.username == "undo_other").first()
        if not other:
            other = User(
                email="undo_other@test.local",
                username="undo_other",
                hashed_password=pwd.hash("undo_other"),
                full_name="Undo Other",
                role_id=role.id,
            )
            db.add(other)
            db.commit()
    finally:
        db.close()

    headers = _auth_header(client, "admin", "admin")
    r = client.post(
        "/api/panel/batch-review",
        json={"version_id": version_id, "item_ids": [row_a_id], "action": "approve"},
        headers=headers,
    )
    undo_token = r.json()["undo_token"]

    other_headers = _auth_header(client, "undo_other", "undo_other")
    r2 = client.post("/api/panel/undo-review", json={"undo_token": undo_token}, headers=other_headers)
    assert r2.status_code == 403


def test_review_item_reject_then_undo_restores_none(client):
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        version_id, row_a_id, _ = _seed_two_line_items(db, prefix="Single")
    finally:
        db.close()

    headers = _auth_header(client, "admin", "admin")
    r = client.post(
        "/api/panel/review-item",
        json={"item_id": row_a_id, "action": "reject", "comment": "needs another look"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    undo_token = r.json()["undo_token"]
    assert undo_token

    db = SessionLocal()
    try:
        row_a = db.query(ForecastLineResult).filter(ForecastLineResult.id == row_a_id).one()
        assert row_a.review_status == "reject"
        assert row_a.review_comment == "needs another look"
    finally:
        db.close()

    r = client.post("/api/panel/undo-review", json={"undo_token": undo_token}, headers=headers)
    assert r.status_code == 200

    db = SessionLocal()
    try:
        row_a = db.query(ForecastLineResult).filter(ForecastLineResult.id == row_a_id).one()
        assert row_a.review_status is None
        assert row_a.review_comment is None
    finally:
        db.close()
