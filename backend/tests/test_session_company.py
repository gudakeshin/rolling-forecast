"""The active company belongs to the session (access-token claim ``cid``),
not to the User row: switching company in one tab or device must not move
the others, and a revoked membership must not keep working."""

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
def member_of_two():
    """A user with an analyst membership in two companies; default is A."""
    from app.database import SessionLocal
    from app.models.business_unit import BusinessUnit
    from app.models.company import Company
    from app.models.company_membership import CompanyMembership
    from app.models.user import Role, User
    from app.services.tenant_scope import UNRESTRICTED, push_tenant_scope, reset_tenant_scope

    suffix = uuid.uuid4().hex[:8]
    token = push_tenant_scope(UNRESTRICTED)
    db = SessionLocal()
    try:
        role = db.query(Role).filter(Role.name == "analyst").first()
        assert role is not None
        co_a, co_b = Company(name=f"SessCo-A-{suffix}"), Company(name=f"SessCo-B-{suffix}")
        db.add_all([co_a, co_b])
        db.flush()
        bu_a = BusinessUnit(name=f"SessBU-A-{suffix}", company_id=co_a.id)
        bu_b = BusinessUnit(name=f"SessBU-B-{suffix}", company_id=co_b.id)
        db.add_all([bu_a, bu_b])
        db.flush()
        user = User(
            email=f"sess-{suffix}@test.local",
            username=f"sess_{suffix}",
            hashed_password=_pwd.hash(f"sess_{suffix}"),
            full_name="Two Companies",
            business_unit_id=bu_a.id,
            business_unit=bu_a.name,
            role_id=role.id,
        )
        db.add(user)
        db.flush()
        db.add_all([
            CompanyMembership(user_id=user.id, company_id=co_a.id, role_id=role.id),
            CompanyMembership(user_id=user.id, company_id=co_b.id, role_id=role.id),
        ])
        db.commit()
        yield {
            "username": user.username,
            "password": f"sess_{suffix}",
            "user_id": user.id,
            "co_a": co_a.id,
            "co_b": co_b.id,
            "bu_a": bu_a.id,
            "bu_b": bu_b.id,
        }
    finally:
        db.close()
        reset_tenant_scope(token)


def _login(client: TestClient, fx: dict) -> dict:
    r = client.post("/api/auth/login", json={"username": fx["username"], "password": fx["password"]})
    assert r.status_code == 200, r.text
    return r.json()


def _me(client: TestClient, access: str) -> dict:
    r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {access}"})
    assert r.status_code == 200, r.text
    return r.json()


def test_switching_in_one_session_does_not_move_another(client, member_of_two):
    fx = member_of_two
    tab1 = _login(client, fx)
    tab2 = _login(client, fx)

    r = client.post(
        "/api/auth/switch-company",
        json={"company_id": fx["co_b"]},
        headers={"Authorization": f"Bearer {tab1['access_token']}"},
    )
    assert r.status_code == 200, r.text
    tab1_access = r.json()["access_token"]
    assert tab1_access

    assert _me(client, tab1_access)["business_unit_id"] == fx["bu_b"]
    # The other tab still works in A, even though the User row now defaults to B.
    assert _me(client, tab2["access_token"])["business_unit_id"] == fx["bu_a"]


def test_refresh_keeps_the_sessions_company(client, member_of_two):
    fx = member_of_two
    tab = _login(client, fx)
    r = client.post(
        "/api/auth/refresh",
        json={"refresh_token": tab["refresh_token"], "company_id": fx["co_b"]},
    )
    assert r.status_code == 200, r.text
    assert _me(client, r.json()["access_token"])["business_unit_id"] == fx["bu_b"]


def test_refresh_ignores_a_company_without_membership(client, member_of_two):
    from app.database import SessionLocal
    from app.models.company import Company
    from app.services.tenant_scope import UNRESTRICTED, push_tenant_scope, reset_tenant_scope

    fx = member_of_two
    token = push_tenant_scope(UNRESTRICTED)
    db = SessionLocal()
    try:
        stranger = Company(name=f"SessCo-X-{uuid.uuid4().hex[:8]}")
        db.add(stranger)
        db.commit()
        stranger_id = stranger.id
    finally:
        db.close()
        reset_tenant_scope(token)

    tab = _login(client, fx)
    r = client.post(
        "/api/auth/refresh",
        json={"refresh_token": tab["refresh_token"], "company_id": stranger_id},
    )
    assert r.status_code == 200, r.text
    assert _me(client, r.json()["access_token"])["business_unit_id"] == fx["bu_a"]


def test_revoked_membership_stops_the_pinned_company(client, member_of_two):
    from app.database import SessionLocal
    from app.models.company_membership import CompanyMembership
    from app.services.tenant_scope import UNRESTRICTED, push_tenant_scope, reset_tenant_scope

    fx = member_of_two
    tab = _login(client, fx)
    r = client.post(
        "/api/auth/switch-company",
        json={"company_id": fx["co_b"]},
        headers={"Authorization": f"Bearer {tab['access_token']}"},
    )
    pinned_b = r.json()["access_token"]

    # Revoke B, and move the default back to A as an admin would.
    token = push_tenant_scope(UNRESTRICTED)
    db = SessionLocal()
    try:
        from app.models.user import User

        db.query(CompanyMembership).filter(
            CompanyMembership.user_id == fx["user_id"],
            CompanyMembership.company_id == fx["co_b"],
        ).delete()
        user = db.query(User).filter(User.id == fx["user_id"]).first()
        user.business_unit_id = fx["bu_a"]
        db.commit()
    finally:
        db.close()
        reset_tenant_scope(token)

    assert _me(client, pinned_b)["business_unit_id"] == fx["bu_a"]


def test_the_session_company_is_never_written_back_to_the_user_row(member_of_two):
    """Applying B for a request, then committing in that request, must leave
    the stored default on A."""
    from app.database import SessionLocal
    from app.models.user import User
    from app.services.company_membership import apply_session_company
    from app.services.tenant_scope import UNRESTRICTED, push_tenant_scope, reset_tenant_scope

    fx = member_of_two
    token = push_tenant_scope(UNRESTRICTED)
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == fx["user_id"]).first()
        assert apply_session_company(db, user, fx["co_b"]) is True
        assert user.business_unit_id == fx["bu_b"]
        user.full_name = "Renamed in a B-pinned request"
        db.commit()
    finally:
        db.close()

    db = SessionLocal()
    try:
        stored = db.query(User).filter(User.id == fx["user_id"]).first()
        assert stored.full_name == "Renamed in a B-pinned request"
        assert stored.business_unit_id == fx["bu_a"]
    finally:
        db.close()
        reset_tenant_scope(token)
