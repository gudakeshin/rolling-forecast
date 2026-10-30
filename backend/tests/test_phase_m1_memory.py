"""M1 memory persistence and recall basics."""

from __future__ import annotations

import asyncio

from app.config import settings
from app.domain.base_skill import SkillContext
from app.domain.skills.core_memory import (
    ConversationSearchSkill,
    CoreMemoryAppendSkill,
    CoreMemoryReplaceSkill,
)
from app.models.conversation import Conversation, Message
from app.models.memory import MemoryBlock
from app.orchestration.context_manager import ContextManager


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
        user_role=user.role_name,
        conversation_id="test-conv",
        user=user,
    )


def test_core_memory_append_and_replace(db_session, seed_users):
    user = seed_users["analyst"]
    ctx = _ctx(db_session, user)
    append = CoreMemoryAppendSkill()
    replace = CoreMemoryReplaceSkill()

    res1 = asyncio.run(
        append.execute(
            {
                "scope": "user",
                "label": "close_cadence",
                "content": "Monthly close completes by WD+4.",
                "char_limit": 200,
            },
            ctx,
        )
    )
    assert res1.success is True

    block = (
        db_session.query(MemoryBlock)
        .filter(MemoryBlock.scope == "user", MemoryBlock.owner_id == user.id, MemoryBlock.label == "close_cadence")
        .one()
    )
    assert "WD+4" in block.content
    assert block.version == 1

    res2 = asyncio.run(
        replace.execute(
            {
                "scope": "user",
                "label": "close_cadence",
                "content": "Monthly close now completes by WD+3.",
                "char_limit": 200,
            },
            ctx,
        )
    )
    assert res2.success is True
    db_session.refresh(block)
    assert "WD+3" in block.content
    assert block.version == 2


def test_conversation_search_scoped_to_user(db_session, seed_users):
    user = seed_users["analyst"]
    other = seed_users["admin"]
    conv_user = Conversation(user_id=user.id, title="Ops assumptions")
    conv_other = Conversation(user_id=other.id, title="Admin thread")
    db_session.add_all([conv_user, conv_other])
    db_session.flush()
    db_session.add_all(
        [
            Message(conversation_id=conv_user.id, role="user", content="Headcount slows in Q4."),
            Message(conversation_id=conv_other.id, role="user", content="Headcount slows in Q4."),
        ]
    )
    db_session.commit()

    ctx = _ctx(db_session, user)
    skill = ConversationSearchSkill()
    result = asyncio.run(skill.execute({"query": "Headcount", "limit": 10}, ctx))
    assert result.success is True
    hits = result.data.get("hits", [])
    assert len(hits) == 1
    assert hits[0]["conversation_id"] == conv_user.id


def test_core_memory_write_rejected_in_worker_context(db_session, seed_users):
    user = seed_users["analyst"]
    ctx = _ctx(db_session, user)
    ctx.conversation_id = "job-123"
    skill = CoreMemoryAppendSkill()
    result = asyncio.run(
        skill.execute(
            {"scope": "user", "label": "x", "content": "y"},
            ctx,
        )
    )
    assert result.success is False
    assert "disabled in worker jobs" in (result.error or result.message)


def test_core_memory_prompt_budget_is_bounded(db_session, seed_users):
    user = seed_users["analyst"]
    conv = Conversation(user_id=user.id, title="Memory bounded")
    db_session.add(conv)
    db_session.flush()
    # Create a large org block + a user block; prompt inclusion should cap by configured budget.
    db_session.add(
        MemoryBlock(
            scope="organization",
            owner_id=None,
            label="org_facts",
            content="A" * max(200, settings.core_memory_prompt_char_budget),
            char_limit=max(200, settings.core_memory_prompt_char_budget),
            version=1,
            updated_by=user.id,
        )
    )
    db_session.add(
        MemoryBlock(
            scope="user",
            owner_id=user.id,
            label="prefs",
            content="B" * 120,
            char_limit=500,
            version=1,
            updated_by=user.id,
        )
    )
    db_session.commit()

    cm = ContextManager(db_session, conv, user)
    sys_ctx = cm.get_system_context()
    assert len(sys_ctx) <= int(settings.core_memory_prompt_char_budget) + 1500
    assert "Core Memory:" in sys_ctx


def test_business_unit_memory_is_keyed_by_id_not_name(db_session, seed_users, seed_roles):
    """A business_unit-scope memory block must be keyed by the real FK
    (User.business_unit_id), not the legacy free-text name -- see migration
    029_heuristic_and_memory_scoping. Also confirms a second company's user
    never sees the first company's organization memory."""
    from app.models.business_unit import BusinessUnit
    from app.models.user import User

    analyst = seed_users["analyst"]
    ctx = _ctx(db_session, analyst)
    append = CoreMemoryAppendSkill()

    res = asyncio.run(
        append.execute(
            {
                "scope": "business_unit",
                "label": "close_calendar",
                "content": "WD+4 close for this company.",
                "char_limit": 500,
            },
            ctx,
        )
    )
    assert res.success is True

    block = (
        db_session.query(MemoryBlock)
        .filter(MemoryBlock.scope == "business_unit", MemoryBlock.label == "close_calendar")
        .one()
    )
    assert block.owner_id == analyst.business_unit_id
    assert block.owner_id != analyst.business_unit  # the legacy name, not the FK

    other_bu = BusinessUnit(name="Other Co (memory scoping test)")
    db_session.add(other_bu)
    db_session.flush()
    other_user = User(
        email="other-co@test.local",
        username="other_co_analyst",
        hashed_password=analyst.hashed_password,
        full_name="Other Co Analyst",
        business_unit_id=other_bu.id,
        role_id=analyst.role_id,
    )
    db_session.add(other_user)
    db_session.commit()

    from app.services.memory_blocks import list_core_memory_for_user

    own_blocks = list_core_memory_for_user(db_session, analyst)
    assert any(b.label == "close_calendar" for b in own_blocks)

    other_blocks = list_core_memory_for_user(db_session, other_user)
    assert not any(b.label == "close_calendar" for b in other_blocks)
