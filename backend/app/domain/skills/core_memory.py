"""Core memory write/read and conversation recall search skills."""

from __future__ import annotations

from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.domain.base_skill import BaseSkill, SkillContext, SkillResult
from app.models.conversation import Conversation, Message
from app.services.memory_blocks import list_core_memory_for_user, upsert_core_memory
from app.services.permissions import resolve_skill_user


def _reject_worker_context(context: SkillContext) -> str | None:
    # Worker jobs use synthetic conversation ids (job-...) and no interactive user.
    if str(getattr(context, "conversation_id", "")).startswith("job-"):
        return "Memory writes are disabled in worker jobs; run this in an interactive chat session."
    return None


class CoreMemoryAppendSkill(BaseSkill):
    @property
    def name(self) -> str:
        return "core_memory_append"

    @property
    def description(self) -> str:
        return "Append durable organizational/user memory for future chats."

    @property
    def required_role(self) -> str | None:
        return "generate"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "scope": {
                    "type": "string",
                    "description": "persona|organization|user|business_unit",
                    "default": "user",
                },
                "label": {"type": "string", "description": "Short memory block label"},
                "content": {"type": "string", "description": "Text to append"},
                "char_limit": {
                    "type": "integer",
                    "description": "Maximum allowed block size",
                    "default": 2000,
                },
            },
            "required": ["label", "content"],
        }

    async def execute(self, params: dict[str, Any], context: SkillContext) -> SkillResult:
        blocked = _reject_worker_context(context)
        if blocked:
            return SkillResult.fail(blocked)
        db: Session = context.db
        user = resolve_skill_user(context)
        if user is None:
            return SkillResult.fail("Could not resolve acting user.")
        try:
            block = upsert_core_memory(
                db,
                actor=user,
                scope=str(params.get("scope") or "user"),
                label=str(params.get("label") or "").strip(),
                content=str(params.get("content") or ""),
                char_limit=int(params.get("char_limit") or 2000),
                replace=False,
            )
        except ValueError as exc:
            return SkillResult.fail(str(exc))
        return SkillResult.ok(
            message=f"Appended memory block '{block.label}' ({block.scope}).",
            data={
                "block_id": block.id,
                "scope": block.scope,
                "owner_id": block.owner_id,
                "label": block.label,
                "char_limit": block.char_limit,
                "version": block.version,
                "chars": len(block.content or ""),
            },
        )


class CoreMemoryReplaceSkill(BaseSkill):
    @property
    def name(self) -> str:
        return "core_memory_replace"

    @property
    def description(self) -> str:
        return "Replace a durable memory block when it gets stale/noisy."

    @property
    def required_role(self) -> str | None:
        return "generate"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "scope": {
                    "type": "string",
                    "description": "persona|organization|user|business_unit",
                    "default": "user",
                },
                "label": {"type": "string", "description": "Existing/new memory block label"},
                "content": {"type": "string", "description": "Replacement text"},
                "char_limit": {
                    "type": "integer",
                    "description": "Maximum allowed block size",
                    "default": 2000,
                },
            },
            "required": ["label", "content"],
        }

    async def execute(self, params: dict[str, Any], context: SkillContext) -> SkillResult:
        blocked = _reject_worker_context(context)
        if blocked:
            return SkillResult.fail(blocked)
        db: Session = context.db
        user = resolve_skill_user(context)
        if user is None:
            return SkillResult.fail("Could not resolve acting user.")
        try:
            block = upsert_core_memory(
                db,
                actor=user,
                scope=str(params.get("scope") or "user"),
                label=str(params.get("label") or "").strip(),
                content=str(params.get("content") or ""),
                char_limit=int(params.get("char_limit") or 2000),
                replace=True,
            )
        except ValueError as exc:
            return SkillResult.fail(str(exc))
        return SkillResult.ok(
            message=f"Replaced memory block '{block.label}' ({block.scope}).",
            data={
                "block_id": block.id,
                "scope": block.scope,
                "owner_id": block.owner_id,
                "label": block.label,
                "char_limit": block.char_limit,
                "version": block.version,
                "chars": len(block.content or ""),
            },
        )


class ConversationSearchSkill(BaseSkill):
    @property
    def name(self) -> str:
        return "conversation_search"

    @property
    def description(self) -> str:
        return "Search prior conversations to recall earlier decisions/context."

    @property
    def required_role(self) -> str | None:
        return "input"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search term"},
                "limit": {"type": "integer", "description": "Max hits", "default": 8},
            },
            "required": ["query"],
        }

    async def execute(self, params: dict[str, Any], context: SkillContext) -> SkillResult:
        db: Session = context.db
        user = resolve_skill_user(context)
        if user is None:
            return SkillResult.fail("Could not resolve acting user.")
        query = str(params.get("query") or "").strip()
        if not query:
            return SkillResult.fail("query is required.")
        limit = max(1, min(25, int(params.get("limit") or 8)))
        pattern = f"%{query}%"

        rows = (
            db.query(
                Message.id,
                Message.conversation_id,
                Message.content,
                Message.created_at,
                Conversation.title,
            )
            .join(Conversation, Conversation.id == Message.conversation_id)
            .filter(Conversation.user_id == user.id)
            .filter(or_(Message.content.ilike(pattern), Conversation.title.ilike(pattern)))
            .order_by(Message.created_at.desc())
            .limit(limit)
            .all()
        )
        hits = [
            {
                "message_id": r.id,
                "conversation_id": r.conversation_id,
                "conversation_title": r.title,
                "snippet": (r.content or "")[:240],
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
        return SkillResult.ok(
            message=f"Found {len(hits)} matching message(s).",
            data={"query": query, "hits": hits},
            content_blocks=[
                self._table_block(
                    title="Conversation search",
                    columns=[
                        {"key": "conversation_title", "label": "Conversation"},
                        {"key": "snippet", "label": "Snippet"},
                        {"key": "created_at", "label": "When"},
                    ],
                    rows=hits,
                )
            ],
        )


class CoreMemoryListSkill(BaseSkill):
    @property
    def name(self) -> str:
        return "core_memory_list"

    @property
    def description(self) -> str:
        return "List active core memory blocks available to this user."

    @property
    def required_role(self) -> str | None:
        return "input"

    @property
    def parameters_schema(self) -> dict:
        return {"type": "object", "properties": {}}

    async def execute(self, params: dict[str, Any], context: SkillContext) -> SkillResult:
        db: Session = context.db
        user = resolve_skill_user(context)
        if user is None:
            return SkillResult.fail("Could not resolve acting user.")
        blocks = list_core_memory_for_user(db, user)
        rows = [
            {
                "scope": b.scope,
                "label": b.label,
                "chars": len(b.content or ""),
                "char_limit": b.char_limit,
                "version": b.version,
            }
            for b in blocks
        ]
        return SkillResult.ok(
            message=f"Loaded {len(rows)} core memory block(s).",
            data={"blocks": rows},
            content_blocks=[
                self._table_block(
                    title="Core memory blocks",
                    columns=[
                        {"key": "scope", "label": "Scope"},
                        {"key": "label", "label": "Label"},
                        {"key": "chars", "label": "Chars"},
                        {"key": "char_limit", "label": "Limit"},
                        {"key": "version", "label": "Version"},
                    ],
                    rows=rows,
                )
            ],
        )
