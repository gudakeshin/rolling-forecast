"""M2 archival memory — insert/search roundtrip and business-unit isolation."""

from __future__ import annotations

import asyncio

import pytest

from app.domain.base_skill import SkillContext
from app.domain.skills.archival_memory import (
    ArchivalMemoryInsertSkill,
    ArchivalMemorySearchSkill,
)
from app.models.audit import AuditEvent
from app.services import archival_memory


@pytest.fixture(autouse=True)
def _clean_archival_store():
    archival_memory.reset_store()
    yield
    archival_memory.reset_store()


def _ctx(db_session, user):
    class _CM:
        def __init__(self, u):
            self.user = u

        def has_permission(self, _):
            return True

    return SkillContext(
        db=db_session,
        context_manager=_CM(user),  # type: ignore[arg-type]
        user_id=user.id,
        user_role=user.role.name if user.role else "analyst",
        conversation_id="test-conv",
        user=user,
    )


def test_insert_search_roundtrip(db_session, seed_users):
    analyst = seed_users["analyst"]
    archival_memory.insert(
        db_session,
        analyst,
        "Marketing spend is approved quarterly by the growth committee.",
        provenance="agent",
    )
    archival_memory.insert(
        db_session,
        analyst,
        "Headcount hiring freeze started in the fourth quarter.",
        provenance="reflection",
    )

    hits = archival_memory.search(analyst, "marketing spend approval", top_k=5, db=db_session)
    assert hits, "expected at least one archival hit"
    assert "Marketing spend" in hits[0]["text"]
    assert hits[0]["metadata"]["provenance"] == "agent"
    assert hits[0]["metadata"]["business_unit"] == analyst.business_unit

    actions = {
        e.action
        for e in db_session.query(AuditEvent)
        .filter(AuditEvent.entity_type == "archival_memory")
        .all()
    }
    assert actions == {"memory.archival.insert", "memory.archival.search"}


def test_search_never_leaks_across_business_units(db_session, seed_users):
    analyst = seed_users["analyst"]  # business_unit = "North America"
    admin = seed_users["admin"]  # can_view_all_bus

    archival_memory.insert(
        db_session,
        admin,
        "EMEA revenue recognition uses a two-day lag.",
        provenance="document",
        business_unit="EMEA",
    )
    archival_memory.insert(
        db_session,
        analyst,
        "North America revenue recognition closes on working day four.",
        provenance="document",
    )

    scoped = archival_memory.search(analyst, "revenue recognition", top_k=10, db=db_session)
    assert scoped, "analyst should see their own BU entry"
    bus = {h["metadata"]["business_unit"] for h in scoped}
    assert bus == {"North America"}

    unrestricted = archival_memory.search(admin, "revenue recognition", top_k=10, db=db_session)
    assert {h["metadata"]["business_unit"] for h in unrestricted} == {
        "EMEA",
        "North America",
    }


def test_insert_rejects_cross_bu_write_and_bad_provenance(db_session, seed_users):
    analyst = seed_users["analyst"]
    with pytest.raises(ValueError):
        archival_memory.insert(
            db_session, analyst, "Some note", provenance="guesswork"
        )
    with pytest.raises(ValueError):
        archival_memory.insert(
            db_session,
            analyst,
            "Some note",
            provenance="agent",
            business_unit="EMEA",
        )


def test_skills_roundtrip_and_reject_worker_context(db_session, seed_users):
    analyst = seed_users["analyst"]
    ctx = _ctx(db_session, analyst)

    insert_result = asyncio.run(
        ArchivalMemoryInsertSkill().execute(
            {
                "text": "Driver-based OpEx planning replaced the spreadsheet in FY26.",
                "provenance": "agent",
            },
            ctx,
        )
    )
    assert insert_result.success is True

    search_result = asyncio.run(
        ArchivalMemorySearchSkill().execute({"query": "driver based OpEx planning"}, ctx)
    )
    assert search_result.success is True
    assert search_result.data["hits"], "expected archival hits from the skill"

    ctx.conversation_id = "job-42"
    blocked = asyncio.run(
        ArchivalMemoryInsertSkill().execute(
            {"text": "worker note", "provenance": "agent"}, ctx
        )
    )
    assert blocked.success is False
    assert "disabled in worker jobs" in (blocked.error or blocked.message)


def test_archival_collection_is_separate_from_documents():
    from app.services import vector_store

    assert vector_store.ARCHIVAL_COLLECTION_NAME != vector_store.COLLECTION_NAME


def test_archival_api_roundtrip():
    import os

    from fastapi.testclient import TestClient

    os.environ["SEED_DEMO_USERS"] = "true"
    os.environ["APP_ENV"] = "test"
    from app.main import app
    from app.rate_limit import limiter

    try:
        limiter.reset()
    except Exception:
        pass

    with TestClient(app) as client:
        login = client.post(
            "/api/auth/login", json={"username": "analyst", "password": "analyst"}
        )
        assert login.status_code == 200, login.text
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        created = client.post(
            "/api/memory/archival",
            headers=headers,
            json={
                "text": "Quota attainment assumptions live in the sales capacity model.",
                "provenance": "agent",
            },
        )
        assert created.status_code == 200, created.text
        assert created.json()["provenance"] == "agent"

        bad = client.post(
            "/api/memory/archival",
            headers=headers,
            json={"text": "note", "provenance": "hunch"},
        )
        assert bad.status_code == 400

        found = client.get(
            "/api/memory/archival/search",
            headers=headers,
            params={"q": "quota attainment assumptions", "top_k": 5},
        )
        assert found.status_code == 200, found.text
        assert found.json()["hits"], "expected archival hit from the API"
