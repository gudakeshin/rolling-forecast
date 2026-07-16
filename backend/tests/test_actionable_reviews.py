"""Tests for actionable review surfaces: capabilities, approvals eligibility, override revert."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from passlib.context import CryptContext

from app.models.approval import ApprovalWorkflow, ApprovalStep
from app.models.forecast import ForecastVersion, ForecastLineResult
from app.models.line_item import LineItem
from app.models.override import Override
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


def test_auth_me_includes_capability_flags(client):
    headers = _auth_header(client, "admin", "admin")
    r = client.get("/api/auth/me", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["role_name"] == "admin"
    assert body["can_admin"] is True
    assert body["can_review"] is True
    assert body["can_override"] is True
    assert body["can_generate"] is True
    assert "can_input" in body
    assert "can_publish" in body


def test_approvals_status_sod_blocks_creator(client):
    from app.database import SessionLocal

    pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
    db = SessionLocal()
    try:
        admin_role = db.query(Role).filter(Role.name == "admin").first()
        assert admin_role is not None

        creator = db.query(User).filter(User.username == "sod_creator").first()
        if not creator:
            creator = User(
                email="sod_creator@test.local",
                username="sod_creator",
                hashed_password=pwd.hash("sod_creator"),
                full_name="SoD Creator",
                role_id=admin_role.id,
            )
            db.add(creator)
            db.flush()

        version = ForecastVersion(
            name="FC-Status-SoD",
            status="in_review",
            version_type="scheduled",
            horizon_months=12,
            created_by=creator.id,
            created_at=datetime.now(timezone.utc),
        )
        db.add(version)
        db.flush()

        wf = ApprovalWorkflow(
            name="Status SoD Workflow",
            description="test",
            is_active=True,
            require_sod=True,
            levels=[{"level": 1, "role": "admin", "label": "Admin"}],
            created_at=datetime.now(timezone.utc),
        )
        db.add(wf)
        db.flush()

        step = ApprovalStep(
            version_id=version.id,
            workflow_id=wf.id,
            level=1,
            required_role="admin",
            status="pending",
            created_at=datetime.now(timezone.utc),
        )
        db.add(step)
        db.commit()
        version_id = version.id
    finally:
        db.close()

    headers = _auth_header(client, "sod_creator", "sod_creator")
    r = client.get(f"/api/approvals/status/{version_id}", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["is_creator"] is True
    assert body["version"]["id"] == version_id
    assert len(body["steps"]) == 1
    assert body["steps"][0]["can_decide"] is False
    assert "segregation of duties" in (body["steps"][0]["blocked_reason"] or "").lower()


def test_revert_override_happy_path(client):
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        admin = db.query(User).filter(User.username == "admin").first()
        assert admin is not None

        li = db.query(LineItem).first()
        if not li:
            li = LineItem(
                name="Test Revenue",
                account_code="4000",
                category="Revenue",
                business_unit="North America",
            )
            db.add(li)
            db.flush()

        version = ForecastVersion(
            name="FC-Revert-Test",
            status="draft",
            version_type="scheduled",
            horizon_months=3,
            created_by=admin.id,
            override_count=1,
            created_at=datetime.now(timezone.utc),
        )
        db.add(version)
        db.flush()

        result = ForecastLineResult(
            version_id=version.id,
            line_item_id=li.id,
            period="2025-01",
            p50=100000.0,
            is_overridden=True,
            override_value=120000.0,
        )
        db.add(result)

        override = Override(
            version_id=version.id,
            line_item_id=li.id,
            period="2025-01",
            original_model_value=100000.0,
            override_value=120000.0,
            reason="Test override for revert endpoint coverage",
            status="active",
            user_id=admin.id,
            created_at=datetime.now(timezone.utc),
        )
        db.add(override)
        db.commit()
        override_id = override.id
        version_id = version.id
        result_id = result.id
    finally:
        db.close()

    headers = _auth_header(client, "admin", "admin")
    r = client.post(f"/api/panel/overrides/{override_id}/revert", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["success"] is True
    assert r.json()["status"] == "reverted"

    db = SessionLocal()
    try:
        ov = db.query(Override).filter(Override.id == override_id).one()
        assert ov.status == "reverted"
        assert ov.reverted_by is not None
        fr = db.query(ForecastLineResult).filter(ForecastLineResult.id == result_id).one()
        assert fr.is_overridden is False
        assert fr.p50 == 100000.0
        ver = db.query(ForecastVersion).filter(ForecastVersion.id == version_id).one()
        assert ver.override_count == 0
    finally:
        db.close()


def test_revert_override_non_active_400(client):
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        admin = db.query(User).filter(User.username == "admin").first()
        li = db.query(LineItem).first()
        if not li:
            li = LineItem(
                name="Test COGS",
                account_code="5000",
                category="COGS",
                business_unit="North America",
            )
            db.add(li)
            db.flush()

        version = ForecastVersion(
            name="FC-Revert-Inactive",
            status="draft",
            version_type="scheduled",
            horizon_months=3,
            created_by=admin.id,
            created_at=datetime.now(timezone.utc),
        )
        db.add(version)
        db.flush()

        override = Override(
            version_id=version.id,
            line_item_id=li.id,
            period="2025-02",
            original_model_value=50000.0,
            override_value=55000.0,
            reason="Already reverted override for 400 test",
            status="reverted",
            user_id=admin.id,
            created_at=datetime.now(timezone.utc),
        )
        db.add(override)
        db.commit()
        override_id = override.id
    finally:
        db.close()

    headers = _auth_header(client, "admin", "admin")
    r = client.post(f"/api/panel/overrides/{override_id}/revert", headers=headers)
    assert r.status_code == 400
    assert "active" in r.json()["detail"].lower()


def test_revert_override_missing_permission_403(client):
    """input_provider (or similar) without override permission gets 403."""
    from app.database import SessionLocal

    pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
    db = SessionLocal()
    try:
        role = db.query(Role).filter(Role.name == "input_provider").first()
        if not role:
            role = Role(
                name="input_provider",
                description="Input only",
                can_input=True,
                can_generate=False,
                can_override=False,
                can_review=False,
                can_publish=False,
                can_admin=False,
            )
            db.add(role)
            db.flush()

        user = db.query(User).filter(User.username == "no_override").first()
        if not user:
            user = User(
                email="no_override@test.local",
                username="no_override",
                hashed_password=pwd.hash("no_override"),
                full_name="No Override",
                role_id=role.id,
            )
            db.add(user)
            db.flush()

        admin = db.query(User).filter(User.username == "admin").first()
        li = db.query(LineItem).first()
        if not li:
            li = LineItem(
                name="Test Opex",
                account_code="6000",
                category="OpEx",
                business_unit="North America",
            )
            db.add(li)
            db.flush()

        version = ForecastVersion(
            name="FC-Revert-403",
            status="draft",
            version_type="scheduled",
            horizon_months=3,
            created_by=admin.id if admin else user.id,
            created_at=datetime.now(timezone.utc),
        )
        db.add(version)
        db.flush()

        override = Override(
            version_id=version.id,
            line_item_id=li.id,
            period="2025-03",
            original_model_value=1000.0,
            override_value=1100.0,
            reason="Permission denied revert test override",
            status="active",
            user_id=admin.id if admin else user.id,
            created_at=datetime.now(timezone.utc),
        )
        db.add(override)
        db.commit()
        override_id = override.id
    finally:
        db.close()

    headers = _auth_header(client, "no_override", "no_override")
    r = client.post(f"/api/panel/overrides/{override_id}/revert", headers=headers)
    assert r.status_code == 403
