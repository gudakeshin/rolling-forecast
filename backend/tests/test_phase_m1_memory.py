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
