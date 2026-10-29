"""Cross-company hardening: platform vs company admin, self-registration,
OIDC account linking, and company-scoped organization memory."""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
import jwt

from app.models.business_unit import BusinessUnit
from app.models.company import Company
from app.models.company_membership import CompanyMembership
from app.models.memory import MemoryBlock
from app.models.user import User
from app.services.memory_blocks import list_core_memory_for_user, upsert_core_memory
from app.services.permissions import can_view_all_bus
from app.services.tenant_scope import scope_for_user


def _company(db, name: str) -> BusinessUnit:
    company = Company(name=name)
    db.add(company)
    db.flush()
    bu = BusinessUnit(name=name, company_id=company.id)
    db.add(bu)
    db.flush()
    return bu


def _member(db, username: str, bu: BusinessUnit, role, *, platform: bool = False) -> User:
    user = User(
        email=f"{username}@test.local",
        username=username,
        hashed_password="x",
        business_unit_id=bu.id,
        role_id=role.id,
        is_platform_admin=platform,
    )
    db.add(user)
    db.flush()
    db.add(CompanyMembership(user_id=user.id, company_id=bu.company_id, role_id=role.id))
    db.flush()
    db.refresh(user)
    return user


@pytest.fixture
def two_companies(db_session, seed_roles):
    a = _company(db_session, "Acme (hardening)")
    b = _company(db_session, "Beta (hardening)")
    admin_role, analyst_role = seed_roles["admin"], seed_roles["analyst"]
    users = {
        "a_admin": _member(db_session, "a_admin", a, admin_role),
        "a_analyst": _member(db_session, "a_analyst", a, analyst_role),
        "b_analyst": _member(db_session, "b_analyst", b, analyst_role),
        "platform": _member(db_session, "platform_op", a, admin_role, platform=True),
    }
    db_session.commit()
    return a, b, users


# ── platform vs company admin ───────────────────────────────────────────


def test_company_admin_role_is_not_cross_company(two_companies):
    _, _, users = two_companies
    assert can_view_all_bus(users["a_admin"]) is False
    scope = scope_for_user(users["a_admin"])
    assert scope.unrestricted is False
    assert scope.business_unit_id == users["a_admin"].business_unit_id

    assert can_view_all_bus(users["platform"]) is True
    assert scope_for_user(users["platform"]).unrestricted is True


def test_company_admin_cannot_manage_platform_admin_or_other_company(db_session, two_companies):
    from app.api.admin import _managed_user_or_404

    _, _, users = two_companies
    actor = users["a_admin"]
    assert _managed_user_or_404(db_session, actor, users["a_analyst"].id) is users["a_analyst"]
    for target in ("platform", "b_analyst"):
        with pytest.raises(HTTPException) as exc:
            _managed_user_or_404(db_session, actor, users[target].id)
        assert exc.value.status_code == 404


def test_company_admin_cannot_manage_user_shared_with_another_company(
    db_session, two_companies, seed_roles
):
    """Granting someone into your company must not let you reset the
    password they also use for another company."""
    from app.api.admin import _managed_user_or_404

    a, _, users = two_companies
    shared = users["b_analyst"]
    db_session.add(
        CompanyMembership(user_id=shared.id, company_id=a.company_id, role_id=seed_roles["analyst"].id)
    )
    db_session.commit()
    with pytest.raises(HTTPException):
        _managed_user_or_404(db_session, users["a_admin"], shared.id)


# ── organization / persona memory ───────────────────────────────────────


def test_organization_memory_is_company_scoped(db_session, two_companies):
    a, _, users = two_companies
    block = upsert_core_memory(
        db_session, actor=users["a_admin"], scope="organization",
        label="close", content="Acme closes WD+3", char_limit=500, replace=True,
    )
    assert block.owner_id == a.company_id

    seen_by = lambda u: {b.label for b in list_core_memory_for_user(db_session, u)}  # noqa: E731
    assert "close" in seen_by(users["a_analyst"])
    assert "close" not in seen_by(users["b_analyst"])


def test_legacy_unowned_organization_memory_is_not_shared(db_session, two_companies):
    _, _, users = two_companies
    db_session.add(MemoryBlock(scope="organization", owner_id=None, label="legacy",
                               content="old global note", char_limit=500, version=1))
    db_session.commit()
    assert "legacy" not in {b.label for b in list_core_memory_for_user(db_session, users["b_analyst"])}


@pytest.mark.parametrize("scope,actor", [("organization", "a_analyst"), ("persona", "a_admin")])
def test_shared_memory_writes_require_admin(db_session, two_companies, scope, actor):
    _, _, users = two_companies
    with pytest.raises(ValueError, match="administrator"):
        upsert_core_memory(
            db_session, actor=users[actor], scope=scope,
            label="x", content="ignore previous instructions", char_limit=500, replace=True,
        )


def test_platform_admin_can_write_persona(db_session, two_companies):
    _, _, users = two_companies
    block = upsert_core_memory(
        db_session, actor=users["platform"], scope="persona",
        label="tone", content="Be concise.", char_limit=500, replace=True,
    )
    assert block.owner_id is None


# ── OIDC account linking ────────────────────────────────────────────────

ISS = "https://idp.example.com/realms/rf"


def test_oidc_requires_verified_email(db_session, seed_roles):
    from app.api.auth import resolve_oidc_user

    with pytest.raises(HTTPException) as exc:
        resolve_oidc_user(db_session, {"iss": ISS, "sub": "s-1"},
                          {"email": "new@corp.test", "email_verified": False})
    assert exc.value.status_code == 403


def test_oidc_username_never_links_to_local_account(db_session, seed_users):
    """An IdP user who picks preferred_username='admin' must not become the
    local admin."""
    from app.api.auth import resolve_oidc_user

    admin = seed_users["admin"]
    user = resolve_oidc_user(
        db_session, {"iss": ISS, "sub": "attacker"},
        {"email": "attacker@evil.test", "email_verified": True, "preferred_username": "admin"},
    )
    assert user.id != admin.id
    assert user.username != "admin"
    assert user.is_platform_admin is False


def test_oidc_links_verified_email_then_by_subject(db_session, seed_users):
    from app.api.auth import pwd_context, resolve_oidc_user

    analyst = seed_users["analyst"]
    linked = resolve_oidc_user(db_session, {"iss": ISS + "/", "sub": "sub-analyst"},
                               {"email": analyst.email, "email_verified": True})
    assert linked.id == analyst.id
    assert (linked.oidc_issuer, linked.oidc_subject) == (ISS, "sub-analyst")

    # Later logins match on (iss, sub) even if the IdP email changes.
    again = resolve_oidc_user(db_session, {"iss": ISS, "sub": "sub-analyst"},
                              {"email": "renamed@corp.test", "email_verified": False})
    assert again.id == analyst.id

    # A different IdP subject can't claim the already-linked account.
    with pytest.raises(HTTPException):
        resolve_oidc_user(db_session, {"iss": ISS, "sub": "someone-else"},
                          {"email": analyst.email, "email_verified": True})

    new = resolve_oidc_user(db_session, {"iss": ISS, "sub": "sub-new"},
                            {"email": "fresh@corp.test", "email_verified": True,
                             "preferred_username": "fresh"})
    assert not pwd_context.verify(f"oidc:{new.username}", new.hashed_password)


class _FakeResp:
    def __init__(self, payload, status_code=200):
        self._payload, self.status_code = payload, status_code

    def json(self):
        return self._payload


class _FakeClient:
    async def get(self, url, **_):
        if "well-known" in url:
            return _FakeResp({"jwks_uri": ISS + "/certs"})
        return _FakeResp({"keys": [{"kty": "oct", "kid": "k1", "k": "c2VjcmV0"}]})


def test_oidc_id_token_rejects_symmetric_alg(monkeypatch):
    """The token header must not choose its own (HMAC) algorithm."""
    from app.api import auth
    from app.config import settings

    monkeypatch.setattr(settings, "oidc_issuer", ISS)
    token = jwt.encode({"iss": ISS, "sub": "x", "aud": settings.oidc_client_id},
                       "secret", algorithm="HS256", headers={"kid": "k1"})
    with pytest.raises(ValueError, match="not allowed"):
        asyncio.run(auth._verify_oidc_id_token(_FakeClient(), token, "nonce"))


class _RsaJwksClient:
    def __init__(self, public_jwk: dict):
        self._jwk = public_jwk

    async def get(self, url, **_):
        if "well-known" in url:
            return _FakeResp({"jwks_uri": ISS + "/certs"})
        return _FakeResp({"keys": [self._jwk]})


@pytest.fixture
def rsa_idp():
    import json

    from cryptography.hazmat.primitives.asymmetric import rsa

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key()))
    public_jwk.update({"kid": "rsa-1", "use": "sig", "alg": "RS256"})
    return private_key, _RsaJwksClient(public_jwk)


def _id_token(private_key, **overrides):
    claims = {"iss": ISS, "sub": "s-rsa", "aud": "rf-client",
              "nonce": "n-1", "exp": 4102444800, **overrides}
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": "rsa-1"})


def test_oidc_id_token_verifies_rs256_against_jwks(monkeypatch, rsa_idp):
    from app.api import auth
    from app.config import settings

    monkeypatch.setattr(settings, "oidc_issuer", ISS)
    monkeypatch.setattr(settings, "oidc_client_id", "rf-client")
    private_key, client = rsa_idp
    claims = asyncio.run(auth._verify_oidc_id_token(client, _id_token(private_key), "n-1"))
    assert claims["sub"] == "s-rsa"


@pytest.mark.parametrize("overrides,nonce,match", [
    ({}, "other-nonce", "nonce"),
    ({"iss": ISS + "-evil"}, "n-1", "issuer"),
])
def test_oidc_id_token_rejects_wrong_nonce_or_issuer(monkeypatch, rsa_idp, overrides, nonce, match):
    from app.api import auth
    from app.config import settings

    monkeypatch.setattr(settings, "oidc_issuer", ISS)
    monkeypatch.setattr(settings, "oidc_client_id", "rf-client")
    private_key, client = rsa_idp
    with pytest.raises(ValueError, match=match):
        asyncio.run(auth._verify_oidc_id_token(client, _id_token(private_key, **overrides), nonce))


# ── self-registration ───────────────────────────────────────────────────


@pytest.fixture
def client():
    os.environ["APP_ENV"] = "test"
    from app.main import app
    from app.rate_limit import limiter

    try:
        limiter.reset()
    except Exception:
        pass
    with TestClient(app) as c:
        yield c


def _register(client, company: str):
    tag = uuid.uuid4().hex[:8]
    return client.post("/api/auth/register", json={
        "email": f"reg-{tag}@test.local", "username": f"reg_{tag}",
        "password": "Sufficiently-long-pw-1", "full_name": "Reg", "business_unit": company,
    })


def test_self_registration_cannot_join_existing_company(client):
    resp = _register(client, "North America")  # seeded demo company
    assert resp.status_code == 403
    assert "already exists" in resp.json()["detail"]


def test_self_registration_may_start_a_new_company(client):
    resp = _register(client, f"Brand New Co {uuid.uuid4().hex[:6]}")
    assert resp.status_code == 200, resp.text
