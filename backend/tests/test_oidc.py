"""OIDC / PKCE / one-time SSO code regressions."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from app.services.oidc_store import (
    create_oidc_pending,
    issue_one_time_code,
    pkce_challenge,
    pop_oidc_pending,
    redeem_one_time_code,
)


def test_pkce_challenge_is_s256_urlsafe():
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    challenge = pkce_challenge(verifier)
    # Known S256 challenge for the RFC 7636 appendix B verifier
    assert challenge == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
    assert "=" not in challenge


def test_oidc_pending_state_is_single_use():
    pending = create_oidc_pending()
    assert pending.state
    assert pending.nonce
    assert pending.code_verifier
    assert pop_oidc_pending(pending.state) is not None
    assert pop_oidc_pending(pending.state) is None  # consumed


def test_one_time_sso_code_is_single_use():
    code = issue_one_time_code("user-abc")
    assert redeem_one_time_code(code) == "user-abc"
    assert redeem_one_time_code(code) is None


def test_oidc_exchange_rejects_invalid_code():
    os.environ["APP_ENV"] = "test"
    os.environ["SEED_DEMO_USERS"] = "true"
    from app.main import app

    with TestClient(app) as client:
        resp = client.post("/api/auth/oidc/exchange", json={"code": "not-a-real-code"})
        assert resp.status_code in (400, 401, 404)


def test_oidc_login_requires_config():
    os.environ["APP_ENV"] = "test"
    os.environ["SEED_DEMO_USERS"] = "true"
    from app.main import app
    from app.config import settings

    prev = settings.oidc_enabled
    settings.oidc_enabled = False
    try:
        with TestClient(app) as client:
            resp = client.get("/api/auth/oidc/login", follow_redirects=False)
            assert resp.status_code in (400, 404, 501)
    finally:
        settings.oidc_enabled = prev


def test_oidc_login_redirect_includes_pkce_when_enabled(monkeypatch):
    os.environ["APP_ENV"] = "test"
    os.environ["SEED_DEMO_USERS"] = "true"
    from app.main import app
    from app.config import settings

    monkeypatch.setattr(settings, "oidc_enabled", True)
    monkeypatch.setattr(settings, "oidc_issuer", "https://idp.example.com/realms/rf")
    monkeypatch.setattr(settings, "oidc_client_id", "rf-client")
    monkeypatch.setattr(settings, "oidc_redirect_uri", "http://localhost:8000/api/auth/oidc/callback")

    with TestClient(app) as client:
        resp = client.get("/api/auth/oidc/login", follow_redirects=False)
        assert resp.status_code in (302, 307)
        loc = resp.headers.get("location", "")
        assert "code_challenge" in loc
        assert "code_challenge_method=S256" in loc
        assert "state=" in loc
        assert "nonce=" in loc
        assert "access_token" not in loc
        assert "sso_code" not in loc
