from __future__ import annotations

"""Core-memory storage helpers with scope-aware ownership."""

from dataclasses import dataclass

from sqlalchemy.orm import Session
from sqlalchemy import or_

from app.config import settings
from app.models.memory import MemoryBlock
from app.models.user import User
from app.services.audit import record_audit

VALID_SCOPES = {"persona", "organization", "user", "business_unit"}


@dataclass(frozen=True)
class MemoryTarget:
    scope: str
    owner_id: str | None


def scope_char_limit(scope: str) -> int:
    if scope == "persona":
        return int(settings.core_memory_char_limit_persona)
    if scope == "organization":
        return int(settings.core_memory_char_limit_organization)
    if scope == "business_unit":
        return int(settings.core_memory_char_limit_business_unit)
    return int(settings.core_memory_char_limit_user)


def _company_id(db: Session, user: User) -> str | None:
    from app.services.company_membership import active_company_id

    return active_company_id(db, user)


def resolve_memory_target(db: Session, user: User, scope: str) -> MemoryTarget:
    """Where a write by `user` to `scope` lands, enforcing who may write it.

    Every block below except ``persona`` is keyed to one owner, so it can
    only reach that owner's prompts. ``organization`` is the active
    company's shared memory (company admins only); ``persona`` -- the one
    block every company's prompt includes -- is platform-admin only, since a
    write there is effectively a system-prompt edit for all tenants.
    """
    from app.services.permissions import is_platform_admin

    if scope not in VALID_SCOPES:
        raise ValueError(f"Invalid scope '{scope}'")
    if scope == "persona":
        if not is_platform_admin(user):
            raise ValueError("Only a platform administrator can write persona memory")
        return MemoryTarget(scope=scope, owner_id=None)
    if scope == "organization":
        if not user.can_admin:
            raise ValueError("Only a company administrator can write organization memory")
        company_id = _company_id(db, user)
        if not company_id:
            raise ValueError("User has no company; cannot write organization memory")
        return MemoryTarget(scope=scope, owner_id=company_id)
    if scope == "user":
        return MemoryTarget(scope=scope, owner_id=user.id)
    if not user.business_unit_id:
        raise ValueError("User has no business_unit; cannot write business_unit memory")
    return MemoryTarget(scope=scope, owner_id=user.business_unit_id)


def _get_block(
    db: Session,
    *,
    scope: str,
    owner_id: str | None,
    label: str,
) -> MemoryBlock | None:
    return (
        db.query(MemoryBlock)
        .filter(
            MemoryBlock.scope == scope,
            MemoryBlock.owner_id == owner_id,
            MemoryBlock.label == label,
        )
        .first()
    )


def upsert_core_memory(
    db: Session,
    *,
    actor: User,
    scope: str,
    label: str,
    content: str,
    char_limit: int,
    replace: bool,
) -> MemoryBlock:
    target = resolve_memory_target(db, actor, scope)
    block = _get_block(db, scope=target.scope, owner_id=target.owner_id, label=label)
    before = block.content if block else ""

    payload = (content or "").strip()
    if replace:
        next_content = payload
    else:
        next_content = f"{before}\n{payload}".strip() if before else payload

    max_scope_limit = scope_char_limit(target.scope)
    effective_limit = max(1, min(int(char_limit), max_scope_limit))
    if len(next_content) > effective_limit:
        raise ValueError(
            f"Memory block '{label}' exceeds char_limit {effective_limit}; use replace with consolidation."
        )

    if block is None:
        block = MemoryBlock(
            scope=target.scope,
            owner_id=target.owner_id,
            label=label,
            content=next_content,
            char_limit=effective_limit,
            version=1,
            updated_by=actor.id,
        )
        db.add(block)
    else:
        block.content = next_content
        block.char_limit = effective_limit
        block.version = int(block.version or 0) + 1
        block.updated_by = actor.id

    db.flush()
    record_audit(
        db,
        action="memory.core.write",
        entity_type="memory_block",
        entity_id=str(block.id),
        actor_id=actor.id,
        actor_username=actor.username,
        details={
            "scope": block.scope,
            "owner_id": block.owner_id,
            "label": block.label,
            "replace": replace,
            "char_limit": block.char_limit,
            "before_chars": len(before),
            "after_chars": len(block.content or ""),
            "version": block.version,
        },
    )
    db.commit()
    db.refresh(block)
    return block


def list_core_memory_for_user(db: Session, user: User) -> list[MemoryBlock]:
    """Every core-memory block `user` may read -- the one visibility rule
    shared by the API, the chat system prompt and the memory skills."""
    clauses = [
        (MemoryBlock.scope == "persona") & (MemoryBlock.owner_id.is_(None)),
        (MemoryBlock.scope == "user") & (MemoryBlock.owner_id == user.id),
    ]
    company_id = _company_id(db, user)
    if company_id:
        clauses.append(
            (MemoryBlock.scope == "organization") & (MemoryBlock.owner_id == company_id)
        )
    if user.business_unit_id:
        clauses.append(
            (MemoryBlock.scope == "business_unit") & (MemoryBlock.owner_id == user.business_unit_id)
        )
    return (
        db.query(MemoryBlock)
        .filter(or_(*clauses))
        .order_by(MemoryBlock.scope.asc(), MemoryBlock.label.asc())
        .all()
    )
