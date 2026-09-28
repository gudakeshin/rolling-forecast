"""Archival memory insert/search skills (M2 long-term recall)."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.domain.base_skill import BaseSkill, SkillContext, SkillResult
from app.services import archival_memory
from app.services.permissions import resolve_skill_user


def _reject_worker_context(context: SkillContext) -> str | None:
    # Worker jobs use synthetic conversation ids (job-...) and no interactive user.
    if str(getattr(context, "conversation_id", "")).startswith("job-"):
        return (
            "Archival memory writes are disabled in worker jobs; "
            "run this in an interactive chat session."
        )
    return None


class ArchivalMemoryInsertSkill(BaseSkill):
    @property
    def name(self) -> str:
        return "archival_memory_insert"

    @property
    def description(self) -> str:
        return "Store a durable note in archival memory for later semantic recall."

    @property
    def required_role(self) -> str | None:
        return "generate"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Note to archive"},
                "provenance": {
                    "type": "string",
                    "description": "agent|document|reflection",
                    "default": "agent",
                },
                "business_unit": {
                    "type": "string",
                    "description": "Business unit scope; defaults to the caller's BU",
                },
            },
            "required": ["text"],
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
            entry = archival_memory.insert(
                db,
                user,
                str(params.get("text") or ""),
                provenance=str(params.get("provenance") or "agent"),
                business_unit=params.get("business_unit"),
            )
        except ValueError as exc:
            return SkillResult.fail(str(exc))
        return SkillResult.ok(
            message=f"Archived {entry['chars']} chars to archival memory ({entry['provenance']}).",
            data=entry,
        )


class ArchivalMemorySearchSkill(BaseSkill):
    @property
    def name(self) -> str:
        return "archival_memory_search"

    @property
    def description(self) -> str:
        return "Semantic search over archival memory for durable facts and past reflections."

    @property
    def required_role(self) -> str | None:
        return "input"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to recall"},
                "top_k": {"type": "integer", "description": "Max hits", "default": 5},
            },
            "required": ["query"],
        }

    async def execute(self, params: dict[str, Any], context: SkillContext) -> SkillResult:
        db: Session = context.db
        user = resolve_skill_user(context)
        if user is None:
            return SkillResult.fail("Could not resolve acting user.")
        try:
            hits = archival_memory.search(
                user,
                str(params.get("query") or ""),
                int(params.get("top_k") or 5),
                db=db,
            )
        except ValueError as exc:
            return SkillResult.fail(str(exc))
        rows = [
            {
                "snippet": (h.get("text") or "")[:240],
                "provenance": (h.get("metadata") or {}).get("provenance"),
                "business_unit": (h.get("metadata") or {}).get("business_unit") or None,
                "score": round(float(h.get("score") or 0.0), 4),
            }
            for h in hits
        ]
        return SkillResult.ok(
            message=f"Recalled {len(rows)} archival memory entr{'y' if len(rows) == 1 else 'ies'}.",
            data={"query": params.get("query"), "hits": hits},
            content_blocks=[
                self._table_block(
                    title="Archival memory",
                    columns=[
                        {"key": "snippet", "label": "Entry"},
                        {"key": "provenance", "label": "Source"},
                        {"key": "score", "label": "Score"},
                    ],
                    rows=rows,
                )
            ],
        )
