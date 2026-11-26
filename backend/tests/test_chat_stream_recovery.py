"""An agent turn finishes and saves its reply even if the client's SSE
connection drops, so the client can recover it by reloading the conversation."""

from __future__ import annotations

import asyncio
import os
import time

import pytest
from fastapi.testclient import TestClient


class _SlowAgent:
    def __init__(self, context_manager, db):
        pass

    async def astream(self, message):
        yield {"event": "token", "data": {"text": "part one"}}
        await asyncio.sleep(0.3)
        yield {
            "event": "message_end",
            "data": {"content": f"reply to {message}", "content_blocks": [], "tool_calls": []},
        }


@pytest.fixture
def client(monkeypatch):
    os.environ["SEED_DEMO_USERS"] = "true"
    os.environ["APP_ENV"] = "test"
    from app.api import chat as chat_api
    from app.main import app
    from app.rate_limit import limiter

    monkeypatch.setattr(chat_api, "MasterAgent", _SlowAgent)
    try:
        limiter.reset()
    except Exception:
        pass
    with TestClient(app) as c:
        yield c


def _headers(client):
    r = client.post("/api/auth/login", json={"username": "analyst", "password": "analyst"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _saved_replies(client, headers, conversation_id):
    r = client.get(f"/api/chat/conversations/{conversation_id}", headers=headers)
    assert r.status_code == 200, r.text
    return [m["content"] for m in r.json()["messages"] if m["role"] == "assistant"]


def test_full_stream_relays_events_and_saves_the_reply(client):
    headers = _headers(client)
    r = client.post("/api/chat/message", json={"content": "hello"}, headers=headers)
    assert r.status_code == 200
    body = r.text
    assert "event: message_start" in body and "event: message_end" in body
    conv_id = body.split('"conversation_id": "')[1].split('"')[0]
    assert _saved_replies(client, headers, conv_id) == ["reply to hello"]


def test_reply_is_saved_after_the_client_disconnects(client):
    headers = _headers(client)
    conv_id = None
    with client.stream("POST", "/api/chat/message", json={"content": "drop me"}, headers=headers) as r:
        for line in r.iter_lines():
            if '"conversation_id"' in line:
                conv_id = line.split('"conversation_id": "')[1].split('"')[0]
                break  # disconnect before message_end
    assert conv_id
    deadline = time.time() + 5
    while time.time() < deadline:
        if _saved_replies(client, headers, conv_id) == ["reply to drop me"]:
            return
        time.sleep(0.1)
    pytest.fail("the reply was not saved after the client disconnected")
