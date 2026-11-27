"""The browser app keeps its refresh token in an httpOnly cookie (opt-in via
the X-Auth-Cookie header); other API clients keep it in the response body."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

COOKIE = {"X-Auth-Cookie": "1"}


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


def _login(client, headers=None):
    return client.post(
        "/api/auth/login", json={"username": "analyst", "password": "analyst"}, headers=headers or {}
    )


def test_cookie_mode_sets_an_httponly_cookie_and_hides_the_token(client):
    r = _login(client, COOKIE)
    assert r.status_code == 200, r.text
    assert r.json()["refresh_token"] is None
    set_cookie = r.headers["set-cookie"].lower()
    assert "rf_refresh=" in set_cookie
    assert "httponly" in set_cookie and "samesite=strict" in set_cookie
    assert "path=/api/auth" in set_cookie


def test_refresh_reads_and_rotates_the_cookie(client):
    _login(client, COOKIE)
    first = client.cookies.get("rf_refresh")
    r = client.post("/api/auth/refresh", json={}, headers=COOKIE)
    assert r.status_code == 200, r.text
    assert r.json()["access_token"] and r.json()["refresh_token"] is None
    rotated = client.cookies.get("rf_refresh")
    assert rotated and rotated != first


def test_refresh_without_any_token_is_401(client):
    client.cookies.clear()
    r = client.post("/api/auth/refresh", json={}, headers=COOKIE)
    assert r.status_code == 401


def test_logout_revokes_and_clears_the_cookie(client):
    _login(client, COOKIE)
    stale = client.cookies.get("rf_refresh")
    r = client.post("/api/auth/logout", json={}, headers=COOKIE)
    assert r.status_code == 200
    assert not client.cookies.get("rf_refresh")
    client.cookies.set("rf_refresh", stale, path="/api/auth")
    assert client.post("/api/auth/refresh", json={}, headers=COOKIE).status_code == 401


def test_api_clients_without_the_header_still_get_the_token_in_the_body(client):
    r = _login(client)
    assert r.json()["refresh_token"]
    assert "rf_refresh" not in r.headers.get("set-cookie", "")
