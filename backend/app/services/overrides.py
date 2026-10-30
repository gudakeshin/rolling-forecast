"""Override apply/revert service — shared by REST endpoints and chat skills."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy.orm import Session

from typing import Protocol

from app.models.forecast import ForecastLineResult, ForecastVersion
from app.models.override import Override
from app.services.audit import record_audit
from app.services.dependency_graph import DependencyGraphManager
from app.services.reconciliation import reconcile_version


def recalculate_and_reconcile(
    db: Session,
    version_id: str,
    changed_line_ids: list[int],
    periods: list[str] | None = None,
) -> int:
    """Recompute dependents (incl. p10/p90) then re-run MinT reconciliation.

    Shared by apply_override, revert, and inline panel overrides.
    Skips MinT when the version has no calculated lines (lone-leaf MinT
    currently nudges p50 without a hierarchy to reconcile).
    """
    dag = DependencyGraphManager(db)
    total = 0
    seen: set[int] = set()
    for line_id in changed_line_ids:
        if line_id in seen:
            continue
        seen.add(line_id)
        total += dag.recalculate_dependents(version_id, line_id, periods)

    has_calculated = (
        db.query(ForecastLineResult.id)
        .filter(
            ForecastLineResult.version_id == version_id,
            ForecastLineResult.is_calculated == True,  # noqa: E712
        )
        .first()
        is not None
    )
    if total > 0 or has_calculated:
        reconcile_version(db, version_id)
    return total


class ActorLike(Protocol):
    """Minimal actor shape: id for attribution, username for the audit trail.

    Deliberately not ``User`` — skills pass a lightweight stand-in built from
    the skill context when no ORM user is loaded, and this function only ever
    reads these two fields.
    """

    @property
    def id(self) -> str: ...

    @property
    def username(self) -> str | None: ...


def revert_override(db: Session, override_id: str, user: ActorLike) -> dict:
    """Revert a single active override and recalculate dependents.

    Sets status/reverted_at/by, restores ForecastLineResult from original_model_value,
    decrements version.override_count, and records an audit event.
    """
    override = db.query(Override).filter(Override.id == override_id).first()
    if not override:
        raise HTTPException(status_code=404, detail="Override not found")
    if override.status != "active":
        raise HTTPException(
            status_code=400,
            detail=f"Override is '{override.status}', only active overrides can be reverted",
        )

    line_result = (
        db.query(ForecastLineResult)
        .filter(
            ForecastLineResult.version_id == override.version_id,
            ForecastLineResult.line_item_id == override.line_item_id,
            ForecastLineResult.period == override.period,
        )
        .first()
    )

    recalc_count = 0
    restored_value = override.original_model_value
    if line_result:
        line_result.is_overridden = False
        line_result.override_value = None
        line_result.p50 = restored_value

        recalc_count = recalculate_and_reconcile(
            db,
            override.version_id,
            [override.line_item_id],
            [override.period],
        )
        # Pin the reverted leaf — MinT may nudge base series slightly
        line_result.p50 = restored_value
        line_result.is_overridden = False
        line_result.override_value = None

    override.status = "reverted"
    override.reverted_at = datetime.now(timezone.utc)
    override.reverted_by = user.id
    override.downstream_recalc_count = recalc_count

    db.flush()

    version = db.query(ForecastVersion).filter(ForecastVersion.id == override.version_id).first()
    if version:
        version.override_count = (
            db.query(ForecastLineResult)
            .filter(
                ForecastLineResult.version_id == override.version_id,
                ForecastLineResult.is_overridden == True,  # noqa: E712
            )
            .count()
        )

    record_audit(
        db,
        action="override.revert",
        entity_type="override",
        entity_id=override.id,
        actor_id=user.id,
        actor_username=user.username,
        details={
            "version_id": override.version_id,
            "line_item_id": override.line_item_id,
            "period": override.period,
            "downstream_recalc": recalc_count,
        },
    )
    db.commit()

    return {
        "success": True,
        "override_id": override.id,
        "status": "reverted",
        "downstream_recalc": recalc_count,
        "version_id": override.version_id,
    }
