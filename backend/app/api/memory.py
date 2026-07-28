"""Memory API — core memory blocks and conversation recall search."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.conversation import Conversation, Message
from app.models.memory import MemoryBlock
from app.models.user import User
from app.services.audit import record_audit
from app.api.auth import get_current_user
from app.services.memory_blocks import scope_char_limit, upsert_core_memory

router = APIRouter(prefix="/memory", tags=["memory"])


class CoreMemoryWrite(BaseModel):
    scope: str = "user"
    label: str
    content: str
    char_limit: int = 2000
    replace: bool = False


@router.get("/core")
async def list_core_memory(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    clauses = [
        (MemoryBlock.scope == "persona") & (MemoryBlock.owner_id.is_(None)),
        (MemoryBlock.scope == "organization") & (MemoryBlock.owner_id.is_(None)),
        (MemoryBlock.scope == "user") & (MemoryBlock.owner_id == current_user.id),
    ]
    if current_user.business_unit:
        clauses.append(
            (MemoryBlock.scope == "business_unit")
            & (MemoryBlock.owner_id == current_user.business_unit)
        )
    rows = (
        db.query(MemoryBlock)
        .filter(or_(*clauses))
        .order_by(MemoryBlock.scope.asc(), MemoryBlock.label.asc())
        .all()
    )
    return {
        "blocks": [
            {
                "id": r.id,
                "scope": r.scope,
                "owner_id": r.owner_id,
                "label": r.label,
                "content": r.content,
                "char_limit": r.char_limit,
                "scope_limit": scope_char_limit(r.scope),
                "version": r.version,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            }
            for r in rows
        ]
    }


@router.post("/core")
async def write_core_memory(
    body: CoreMemoryWrite,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        block = upsert_core_memory(
            db,
            actor=current_user,
            scope=body.scope,
            label=(body.label or "").strip(),
            content=body.content,
            char_limit=body.char_limit,
            replace=body.replace,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {
        "id": block.id,
        "scope": block.scope,
        "owner_id": block.owner_id,
        "label": block.label,
        "char_limit": block.char_limit,
        "version": block.version,
    }


@router.get("/core/limits")
async def core_memory_limits(
    current_user: User = Depends(get_current_user),
):
    return {
        "limits": {
            "persona": scope_char_limit("persona"),
            "organization": scope_char_limit("organization"),
            "user": scope_char_limit("user"),
            "business_unit": scope_char_limit("business_unit"),
        },
        "prompt_budget": settings.core_memory_prompt_char_budget,
        "business_unit": current_user.business_unit,
    }


@router.get("/search")
async def search_conversations(
    q: str = Query(..., min_length=1),
    limit: int = Query(8, ge=1, le=25),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    pattern = f"%{q}%"
    rows = (
        db.query(
            Message.id,
            Message.conversation_id,
            Message.content,
            Message.created_at,
            Conversation.title,
        )
        .join(Conversation, Conversation.id == Message.conversation_id)
        .filter(Conversation.user_id == current_user.id)
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
    record_audit(
        db,
        action="memory.recall.search",
        entity_type="conversation",
        entity_id=None,
        actor_id=current_user.id,
        actor_username=current_user.username,
        details={"query": q, "result_count": len(hits)},
        commit=True,
    )
    return {"query": q, "hits": hits}
