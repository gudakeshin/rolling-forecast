"""Server-side saved views: ownership, company sharing, and isolation."""

from __future__ import annotations

import os
import uuid

import pytest
from fastapi.testclient import TestClient
from passlib.context import CryptContext

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")


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


@pytest.fixture
def people():
    """Two users in company A, one in company B (fresh rows per test)."""
    from app.database import SessionLocal
    from app.models.business_unit import BusinessUnit
    from app.models.company import Company
    from app.models.user import Role, User

    tag = uuid.uuid4().hex[:8]
    db = SessionLocal()
    try:
        role = db.query(Role).filter(Role.name == "analyst").first()
        assert role is not None
        out = {}
        bus = {}
        for side in ("A", "B"):
            company = Company(name=f"SavedViewsCo-{side}-{tag}")
            db.add(company)
            db.flush()
            bu = BusinessUnit(name=f"SavedViewsBU-{side}-{tag}", company_id=company.id)
            db.add(bu)
            db.flush()
            bus[side] = bu.id
        for key, side in (("owner", "A"), ("colleague", "A"), ("outsider", "B")):
            username = f"sv_{key}_{tag}"
            db.add(
                User(
                    email=f"{username}@test.local",
                    username=username,
                    hashed_password=_pwd.hash(username),
                    full_name=key,
                    business_unit_id=bus[side],
                    role_id=role.id,
                )
            )
            out[key] = username
        db.commit()
        return out
    finally:
        db.close()


def _headers(client: TestClient, username: str) -> dict[str, str]:
    r = client.post("/api/auth/login", json={"username": username, "password": username})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _create(client, headers, **overrides):
    body = {"name": "Q3 variance", "panel_type": "variance", "panel_params": {"period": "2026-09"}}
    body.update(overrides)
    r = client.post("/api/saved-views", json=body, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def test_create_and_list_own_view(client, people):
    h = _headers(client, people["owner"])
    created = _create(client, h)
    assert created["is_owner"] is True
    assert created["is_shared"] is False
    assert created["panel_params"] == {"period": "2026-09"}

    views = client.get("/api/saved-views", headers=h).json()["views"]
    assert [v["id"] for v in views] == [created["id"]]


def test_private_view_hidden_from_colleague_until_shared(client, people):
    owner = _headers(client, people["owner"])
    colleague = _headers(client, people["colleague"])
    view = _create(client, owner)

    assert client.get("/api/saved-views", headers=colleague).json()["views"] == []

    r = client.patch(f"/api/saved-views/{view['id']}", json={"is_shared": True}, headers=owner)
    assert r.status_code == 200, r.text
    seen = client.get("/api/saved-views", headers=colleague).json()["views"]
    assert [v["id"] for v in seen] == [view["id"]]
    assert seen[0]["is_owner"] is False


def test_shared_view_never_reaches_another_company(client, people):
    owner = _headers(client, people["owner"])
    outsider = _headers(client, people["outsider"])
    view = _create(client, owner, is_shared=True)

    assert client.get("/api/saved-views", headers=outsider).json()["views"] == []
    r = client.delete(f"/api/saved-views/{view['id']}", headers=outsider)
    assert r.status_code == 404


def test_only_owner_can_change_or_delete(client, people):
    owner = _headers(client, people["owner"])
    colleague = _headers(client, people["colleague"])
    view = _create(client, owner, is_shared=True)

    assert (
        client.patch(f"/api/saved-views/{view['id']}", json={"name": "x"}, headers=colleague).status_code
        == 403
    )
    assert client.delete(f"/api/saved-views/{view['id']}", headers=colleague).status_code == 403

    r = client.patch(f"/api/saved-views/{view['id']}", json={"name": "  Renamed  "}, headers=owner)
    assert r.json()["name"] == "Renamed"
    assert client.delete(f"/api/saved-views/{view['id']}", headers=owner).status_code == 204
    assert client.get("/api/saved-views", headers=owner).json()["views"] == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"name": "   "},
        {"name": "x" * 121},
        {"panel_type": ""},
        {"panel_params": {"blob": "x" * 9000}},
    ],
)
def test_rejects_invalid_input(client, people, overrides):
    h = _headers(client, people["owner"])
    body = {"name": "ok", "panel_type": "variance", "panel_params": {}}
    body.update(overrides)
    assert client.post("/api/saved-views", json=body, headers=h).status_code == 422


def test_requires_authentication(client):
    assert client.get("/api/saved-views").status_code in (401, 403)
