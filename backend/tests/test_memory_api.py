"""Memory API smoke tests."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
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
    token = r.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_core_memory_write_and_list(client: TestClient):
    headers = _auth_header(client, "analyst", "analyst")
    w = client.post(
        "/api/memory/core",
        headers=headers,
        json={
            "scope": "user",
            "label": "close_cadence",
            "content": "Close runs by WD+4.",
            "char_limit": 500,
            "replace": True,
        },
    )
    assert w.status_code == 200, w.text
    l = client.get("/api/memory/core", headers=headers)
    assert l.status_code == 200, l.text
    labels = {b["label"] for b in l.json().get("blocks", [])}
    assert "close_cadence" in labels


def test_memory_search_scoped(client: TestClient):
    headers = _auth_header(client, "analyst", "analyst")
    s = client.get("/api/memory/search", headers=headers, params={"q": "close", "limit": 5})
    assert s.status_code == 200, s.text
    assert "hits" in s.json()
