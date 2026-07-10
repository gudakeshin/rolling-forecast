"""Phase 1 security hardening regressions."""

from __future__ import annotations

import io
import os
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.models.line_item import LineItem
from app.models.user import Role, User
from app.services.integration_safety import (
    assert_safe_integration_url,
    is_private_host,
    resolve_erp_url,
    validate_select_query,
)
from app.services.permissions import can_view_all_bus, line_item_scope_filter
from passlib.context import CryptContext


@pytest.fixture
def client():
    """HTTP client using app lifespan (seeds demo users when SEED_DEMO_USERS=true)."""
    os.environ["SEED_DEMO_USERS"] = "true"
    os.environ["APP_ENV"] = "test"
    from app.main import app
    from app.rate_limit import limiter

    # Reset in-memory rate-limit counters between tests
    try:
        limiter.reset()
    except Exception:
        storage = getattr(limiter, "_storage", None)
        if storage is not None and hasattr(storage, "reset"):
            storage.reset()

    with TestClient(app) as c:
        yield c


def test_validate_select_rejects_drop():
    with pytest.raises(ValueError, match="SELECT"):
        validate_select_query("DROP TABLE users")


def test_validate_select_rejects_multi_statement():
    with pytest.raises(ValueError, match="single SELECT"):
        validate_select_query("SELECT 1; SELECT 2")


def test_validate_select_allows_simple_select():
    q = validate_select_query("SELECT account_code, period, value FROM actuals")
    assert "SELECT" in q.upper()


def test_erp_rejects_absolute_url():
    with pytest.raises(ValueError, match="Absolute"):
        resolve_erp_url("https://erp.example.com/api/", "https://evil.com/steal")


def test_erp_rejects_private_host():
    with pytest.raises(ValueError, match="private"):
        resolve_erp_url("http://127.0.0.1:8080/", "v1/actuals")


def test_erp_joins_relative_path(monkeypatch):
    monkeypatch.setattr(
        "socket.getaddrinfo",
        lambda *a, **k: [(None, None, None, None, ("93.184.216.34", 0))],
    )
    url = resolve_erp_url("https://erp.example.com/api/", "v1/actuals")
    assert url == "https://erp.example.com/api/v1/actuals"


def test_is_private_host_resolves_dns(monkeypatch):
    """Hostnames that resolve to RFC1918 addresses must be blocked (SSRF)."""

    def fake_getaddrinfo(host, *args, **kwargs):
        assert host == "evil.example.com"
        return [(None, None, None, None, ("10.0.0.5", 0))]

    monkeypatch.setattr("socket.getaddrinfo", fake_getaddrinfo)
    assert is_private_host("evil.example.com") is True


def test_is_private_host_allows_public_dns(monkeypatch):
    def fake_getaddrinfo(host, *args, **kwargs):
        return [(None, None, None, None, ("93.184.216.34", 0))]

    monkeypatch.setattr("socket.getaddrinfo", fake_getaddrinfo)
    assert is_private_host("example.com") is False


def test_is_private_host_fail_closed_on_dns_error(monkeypatch):
    import socket as sock

    def boom(*args, **kwargs):
        raise sock.gaierror(8, "nodename nor servname provided")

    monkeypatch.setattr("socket.getaddrinfo", boom)
    assert is_private_host("unresolvable.invalid") is True


def test_assert_safe_integration_url_blocks_private():
    with pytest.raises(ValueError, match="private"):
        assert_safe_integration_url("https://127.0.0.1/api", "erp")
    with pytest.raises(ValueError, match="private"):
        assert_safe_integration_url("postgresql://u:p@10.0.0.1:5432/db", "warehouse")


def test_warehouse_pull_rejects_raw_connection_url(client):
    """Caller-supplied connection_url must be rejected (422 — field removed)."""
    login = client.post("/api/auth/login", json={"username": "analyst", "password": "analyst"})
    assert login.status_code == 200, login.text
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    r = client.post(
        "/api/integrations/warehouse/pull",
        json={
            "connection_url": "postgresql://evil:evil@attacker/db",
            "query": "SELECT 1",
        },
        headers=headers,
    )
    assert r.status_code == 422


def test_upload_rejects_path_traversal(client):
    login = client.post("/api/auth/login", json={"username": "analyst", "password": "analyst"})
    assert login.status_code == 200
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    content = b"account_code,account_name,period,value\nA,A,2024-01,1\n"
    r = client.post(
        "/api/upload/actuals",
        headers=headers,
        files={"file": ("../../etc/passwd.csv", io.BytesIO(content), "text/csv")},
    )
    assert r.status_code == 200
    stored = r.json()["file_path"]
    assert ".." not in stored
    assert stored.endswith(".csv")
    assert os.path.basename(stored) != "passwd.csv"
    assert os.path.exists(stored)


def test_upload_oversized_returns_413(client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "max_upload_bytes", 64)
    login = client.post("/api/auth/login", json={"username": "analyst", "password": "analyst"})
    assert login.status_code == 200
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    big = b"x" * 200
    r = client.post(
        "/api/upload/actuals",
        headers=headers,
        files={"file": ("big.csv", io.BytesIO(big), "text/csv")},
    )
    assert r.status_code == 413


def test_bu_scope_blocks_cross_bu_line_items(db_session, seed_roles):
    pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
    analyst_role = seed_roles["analyst"]
    analyst_role.can_view_all_bus = False
    user = User(
        email="bu@test.local",
        username="bu_analyst",
        hashed_password=pwd.hash("bu_analyst"),
        full_name="BU Analyst",
        business_unit="North America",
        role_id=analyst_role.id,
    )
    db_session.add(user)
    db_session.add_all([
        LineItem(account_code="NA-1", name="NA Rev", category="Revenue", business_unit="North America"),
        LineItem(account_code="EU-1", name="EU Rev", category="Revenue", business_unit="Europe"),
        LineItem(account_code="SHARED", name="Shared", category="Revenue", business_unit=None),
    ])
    db_session.commit()
    db_session.refresh(user)
    user.role = analyst_role

    assert can_view_all_bus(user) is False
    q = line_item_scope_filter(db_session.query(LineItem), user, LineItem)
    codes = {li.account_code for li in q.all()}
    assert codes == {"NA-1", "SHARED"}
    assert "EU-1" not in codes


def test_scoped_line_items_helper_respects_bu(db_session, seed_roles):
    from app.services.permissions import scoped_line_items

    pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
    role = seed_roles["analyst"]
    role.can_view_all_bus = False
    user = User(
        email="scope@test.local",
        username="scope_analyst",
        hashed_password=pwd.hash("scope_analyst"),
        full_name="Scope Analyst",
        business_unit="Europe",
        role_id=role.id,
    )
    db_session.add(user)
    db_session.add_all([
        LineItem(account_code="NA-2", name="NA", category="Revenue", business_unit="North America"),
        LineItem(account_code="EU-2", name="EU", category="Revenue", business_unit="Europe"),
    ])
    db_session.commit()
    user.role = role

    codes = {li.account_code for li in scoped_line_items(db_session, user).all()}
    assert codes == {"EU-2"}


def test_login_rate_limit_returns_429(client):
    """Brute-force login attempts should eventually 429."""
    saw_429 = False
    for _ in range(25):
        r = client.post("/api/auth/login", json={"username": "nope", "password": "wrong"})
        if r.status_code == 429:
            saw_429 = True
            break
    assert saw_429, "Expected 429 after repeated failed logins"
