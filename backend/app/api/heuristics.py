"""Learned heuristics API — list reflection candidates and trigger a pass (M3)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.auth import get_current_user
from app.database import get_db
from app.models.heuristic import (
    HEURISTIC_KINDS,
    HEURISTIC_STATUSES,
    LearnedHeuristic,
)
from app.models.user import User
from app.services.permissions import can_access_business_unit, can_view_all_bus, user_has_permission
from app.services.reflection import (
    MAX_CANDIDATES,
    MIN_CYCLES,
    influences_selection,
    promote_heuristic,
    reject_heuristic,
    run_reflection_pass,
)

router = APIRouter(prefix="/heuristics", tags=["heuristics"])


def _require_reviewer(current_user: User = Depends(get_current_user)) -> User:
    """Reflection passes are reviewer/admin territory — they propose policy."""
    if user_has_permission(current_user, "review") or user_has_permission(
        current_user, "admin"
    ):
        return current_user
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Permission 'review' or 'admin' required",
    )


class ReflectionRunRequest(BaseModel):
    min_cycles: int = Field(default=MIN_CYCLES, ge=2, le=24)
    max_candidates: int = Field(default=MAX_CANDIDATES, ge=1, le=25)
    kinds: list[str] | None = None
    # Only a cross-BU (can_view_all_bus) caller may set this to a company
    # other than their own; see _resolve_run_business_unit below.
    business_unit_id: str | None = None


def _resolve_run_business_unit(current_user: User, requested: str | None) -> str | None:
    """Which company a reflection pass runs for.

    Mirrors app/api/admin_fx.py's `_resolve_settings_business_unit`: an
    explicit id wins only for a cross-BU caller; otherwise a company-scoped
    caller always runs their own company, and a cross-BU caller with none
    specified runs unscoped (every company at once) -- matching this route's
    pre-scoping behavior for admins who want a global sweep.
    """
    if requested:
        if not can_access_business_unit(current_user, requested):
            raise HTTPException(404, "Business unit not found")
        return requested
    if not can_view_all_bus(current_user) and current_user.business_unit_id:
        return current_user.business_unit_id
    return None


def _serialize(row: LearnedHeuristic) -> dict:
    return {
        "id": row.id,
        "scope": row.scope,
        "line_item_id": row.line_item_id,
        "business_unit_id": row.business_unit_id,
        "category": row.category,
        "model_type": row.model_type,
        "horizon_bucket": row.horizon_bucket,
        "kind": row.kind,
        "statement": row.statement,
        "effect_size": row.effect_size,
        "evidence": row.evidence,
        "status": row.status,
        "source": row.source,
        "proposed_at": row.proposed_at.isoformat() if row.proposed_at else None,
        "approved_by": row.approved_by,
        "review_by": row.review_by.isoformat() if row.review_by else None,
        # Promotion makes a heuristic visible; only actuals-derived ones are
        # allowed anywhere near model selection.
        "influences_selection": influences_selection(row),
    }


@router.get("")
async def list_heuristics(
    status_filter: str | None = Query(None, alias="status"),
    kind: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if status_filter and status_filter not in HEURISTIC_STATUSES:
        raise HTTPException(400, f"Invalid status '{status_filter}'")
    if kind and kind not in HEURISTIC_KINDS:
        raise HTTPException(400, f"Invalid kind '{kind}'")

    q = db.query(LearnedHeuristic)
    if not can_view_all_bus(current_user):
        q = q.filter(LearnedHeuristic.business_unit_id == current_user.business_unit_id)
    if status_filter:
        q = q.filter(LearnedHeuristic.status == status_filter)
    if kind:
        q = q.filter(LearnedHeuristic.kind == kind)
    rows = q.order_by(LearnedHeuristic.proposed_at.desc(), LearnedHeuristic.id.desc()).limit(limit).all()
    return {"heuristics": [_serialize(r) for r in rows], "count": len(rows)}


@router.post("/run")
async def run_reflection(
    body: ReflectionRunRequest | None = None,
    current_user: User = Depends(_require_reviewer),
    db: Session = Depends(get_db),
):
    payload = body or ReflectionRunRequest()
    kinds = payload.kinds
    if kinds:
        invalid = [k for k in kinds if k not in HEURISTIC_KINDS]
        if invalid:
            raise HTTPException(400, f"Invalid kinds: {invalid}")
    business_unit_id = _resolve_run_business_unit(current_user, payload.business_unit_id)
    summary = run_reflection_pass(
        db,
        actor=current_user,
        business_unit_id=business_unit_id,
        min_cycles=payload.min_cycles,
        max_candidates=payload.max_candidates,
        kinds=kinds,
    )
    return summary


@router.post("/{heuristic_id}/promote")
async def promote(
    heuristic_id: int,
    current_user: User = Depends(_require_reviewer),
    db: Session = Depends(get_db),
):
    try:
        row = promote_heuristic(db, heuristic_id, actor=current_user)
    except ValueError as e:
        raise HTTPException(404 if "not found" in str(e) else 400, str(e)) from e
    return {
        **_serialize(row),
        "superseded_ids": row.superseded_ids or [],
    }


@router.post("/{heuristic_id}/reject")
async def reject(
    heuristic_id: int,
    current_user: User = Depends(_require_reviewer),
    db: Session = Depends(get_db),
):
    try:
        row = reject_heuristic(db, heuristic_id, actor=current_user)
    except ValueError as e:
        raise HTTPException(404 if "not found" in str(e) else 400, str(e)) from e
    return _serialize(row)
