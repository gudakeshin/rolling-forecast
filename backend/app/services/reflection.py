"""Reflection pass — mine closed forecast cycles for candidate heuristics (M3).

Every number here comes from arithmetic over accuracy records and overrides; no
LLM is involved in detection. The pass only ever writes ``candidate`` rows — a
reviewer must promote them to ``active`` before they influence anything.

Promotion is not the same as consumption. An ``active`` heuristic derived from
reviewer overrides (``source == "overrides"``) is a record of human behaviour,
not evidence about the world, so it never touches model selection — promoting it
only makes it visible. Only ``source == "actuals"`` heuristics are eligible to
nudge a forecast, and even then the caller applies its own guards.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any, Iterable, Sequence

from sqlalchemy.orm import Session

from app.models.heuristic import LearnedHeuristic
from app.models.line_item import LineItem
from app.models.override import Override
from app.models.fx import ForecastAccuracyRecord
from app.models.user import User
from app.services.audit import record_audit

logger = logging.getLogger(__name__)

MIN_CYCLES = 3
MAX_CANDIDATES = 5
MIN_ABS_BIAS_PCT = 2.0
MIN_DIRECTION_CONSISTENCY = 0.67
MIN_ABS_OVERRIDE_PCT = 1.0
CANDIDATE_REVIEW_DAYS = 90

# Only these sources may influence model selection once active. Override-derived
# heuristics describe reviewer habit, so consuming them would close the loop on
# ourselves rather than on reality.
INFLUENCING_SOURCES = frozenset({"actuals"})


def horizon_bucket(offset: Any) -> str:
    """Bucket a horizon offset (months ahead) into a coarse band."""
    try:
        h = int(offset)
    except (TypeError, ValueError):
        return "unknown"
    if h <= 3:
        return "1-3"
    if h <= 6:
        return "4-6"
    if h <= 12:
        return "7-12"
    return "13+"


def _field(record: Any, name: str) -> Any:
    if isinstance(record, dict):
        return record.get(name)
    return getattr(record, name, None)


def _signed_pct_error(predicted: Any, actual: Any) -> float | None:
    """Signed forecast error as a percentage of actual (positive = over-forecast)."""
    try:
        p = float(predicted)
        a = float(actual)
    except (TypeError, ValueError):
        return None
    if a == 0:
        return None
    return (p - a) / abs(a) * 100.0


def detect_error_bias(
    records: Iterable[Any],
    *,
    min_cycles: int = MIN_CYCLES,
    max_candidates: int = MAX_CANDIDATES,
    min_abs_bias_pct: float = MIN_ABS_BIAS_PCT,
    min_consistency: float = MIN_DIRECTION_CONSISTENCY,
) -> list[dict[str, Any]]:
    """Find persistent directional bias per (line item, model, horizon bucket).

    ``records`` are accuracy observations (ORM rows or dicts) exposing
    ``line_item_id``, ``model_type``, ``horizon_offset``, ``predicted_p50``,
    ``actual``, ``version_id`` and ``period``. A group only qualifies once it has
    at least ``min_cycles`` distinct forecast versions (closed cycles) behind it.
    """
    groups: dict[tuple[Any, Any, str], list[dict[str, Any]]] = defaultdict(list)
    for rec in records:
        line_item_id = _field(rec, "line_item_id")
        if line_item_id is None:
            continue
        pct = _signed_pct_error(_field(rec, "predicted_p50"), _field(rec, "actual"))
        if pct is None:
            continue
        key = (
            line_item_id,
            _field(rec, "model_type"),
            horizon_bucket(_field(rec, "horizon_offset")),
        )
        groups[key].append(
            {
                "pct": pct,
                "version_id": _field(rec, "version_id"),
                "period": _field(rec, "period"),
            }
        )

    candidates: list[dict[str, Any]] = []
    for (line_item_id, model_type, bucket), obs in groups.items():
        cycles = {o["version_id"] for o in obs if o["version_id"] is not None}
        if len(cycles) < min_cycles:
            continue
        pcts = [o["pct"] for o in obs]
        mean_pct = sum(pcts) / len(pcts)
        if abs(mean_pct) < min_abs_bias_pct:
            continue
        same_sign = sum(1 for p in pcts if (p > 0) == (mean_pct > 0))
        consistency = same_sign / len(pcts)
        if consistency < min_consistency:
            continue

        direction = "over" if mean_pct > 0 else "under"
        candidates.append(
            {
                "kind": "error_bias",
                "scope": "line_item",
                "line_item_id": line_item_id,
                "model_type": model_type,
                "horizon_bucket": bucket,
                "source": "actuals",
                "effect_size": round(mean_pct, 4),
                "statement": (
                    f"Line item {line_item_id} is {direction}-forecast by "
                    f"{abs(mean_pct):.1f}% on average at horizon {bucket} months"
                    + (f" with model {model_type}" if model_type else "")
                    + f" ({len(cycles)} closed cycles)."
                ),
                "evidence": {
                    "cycles": len(cycles),
                    "observations": len(pcts),
                    "mean_signed_pct_error": round(mean_pct, 4),
                    "median_signed_pct_error": round(median(pcts), 4),
                    "direction_consistency": round(consistency, 4),
                    "direction": direction,
                    "periods": sorted({str(o["period"]) for o in obs if o["period"]}),
                },
            }
        )

    candidates.sort(key=lambda c: (-abs(c["effect_size"]), str(c["line_item_id"])))
    return candidates[:max_candidates]


def detect_override_patterns(
    overrides: Iterable[Any],
    *,
    min_cycles: int = MIN_CYCLES,
    max_candidates: int = MAX_CANDIDATES,
    min_abs_pct: float = MIN_ABS_OVERRIDE_PCT,
) -> list[dict[str, Any]]:
    """Find line items nudged the same direction across ``min_cycles`` cycles.

    ``overrides`` expose ``line_item_id``, ``version_id``, ``period``,
    ``original_model_value`` and ``override_value``.
    """
    groups: dict[tuple[Any, str], list[dict[str, Any]]] = defaultdict(list)
    for ovr in overrides:
        line_item_id = _field(ovr, "line_item_id")
        if line_item_id is None:
            continue
        try:
            original = float(_field(ovr, "original_model_value"))
            new_value = float(_field(ovr, "override_value"))
        except (TypeError, ValueError):
            continue
        delta = new_value - original
        if delta == 0 or original == 0:
            continue
        direction = "up" if delta > 0 else "down"
        groups[(line_item_id, direction)].append(
            {
                "pct": delta / abs(original) * 100.0,
                "version_id": _field(ovr, "version_id"),
                "period": _field(ovr, "period"),
            }
        )

    candidates: list[dict[str, Any]] = []
    for (line_item_id, direction), obs in groups.items():
        cycles = {o["version_id"] for o in obs if o["version_id"] is not None}
        if len(cycles) < min_cycles:
            continue
        pcts = [o["pct"] for o in obs]
        mean_pct = sum(pcts) / len(pcts)
        if abs(mean_pct) < min_abs_pct:
            continue
        candidates.append(
            {
                "kind": "override_pattern",
                "scope": "line_item",
                "line_item_id": line_item_id,
                "model_type": None,
                "horizon_bucket": None,
                "source": "overrides",
                "effect_size": round(mean_pct, 4),
                "statement": (
                    f"Reviewers consistently adjust line item {line_item_id} "
                    f"{direction} by {abs(mean_pct):.1f}% on average "
                    f"across {len(cycles)} cycles."
                ),
                "evidence": {
                    "cycles": len(cycles),
                    "observations": len(pcts),
                    "mean_pct_adjustment": round(mean_pct, 4),
                    "median_pct_adjustment": round(median(pcts), 4),
                    "direction": direction,
                    "periods": sorted({str(o["period"]) for o in obs if o["period"]}),
                },
            }
        )

    candidates.sort(key=lambda c: (-abs(c["effect_size"]), str(c["line_item_id"])))
    return candidates[:max_candidates]


def _same_sign(a: Any, b: Any) -> bool:
    try:
        return (float(a) > 0) == (float(b) > 0)
    except (TypeError, ValueError):
        return False


def _find_existing(
    db: Session, candidate: dict[str, Any]
) -> tuple[LearnedHeuristic | None, LearnedHeuristic | None]:
    """Return (open candidate row, active row) matching this candidate's signature."""
    rows = (
        db.query(LearnedHeuristic)
        .filter(
            LearnedHeuristic.kind == candidate["kind"],
            LearnedHeuristic.line_item_id == candidate["line_item_id"],
            LearnedHeuristic.model_type == candidate["model_type"],
            LearnedHeuristic.horizon_bucket == candidate["horizon_bucket"],
            LearnedHeuristic.status.in_(("candidate", "active")),
        )
        .all()
    )
    open_candidate = None
    active = None
    for row in rows:
        if not _same_sign(row.effect_size, candidate["effect_size"]):
            continue
        if row.status == "active" and active is None:
            active = row
        elif row.status == "candidate" and open_candidate is None:
            open_candidate = row
    return open_candidate, active


def run_reflection_pass(
    db: Session,
    *,
    actor: User | None = None,
    min_cycles: int = MIN_CYCLES,
    max_candidates: int = MAX_CANDIDATES,
    kinds: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Detect heuristics from closed cycles and persist them as candidates."""
    selected = set(kinds or ("error_bias", "override_pattern"))

    detected: list[dict[str, Any]] = []
    if "error_bias" in selected:
        records = (
            db.query(ForecastAccuracyRecord)
            .join(LineItem, LineItem.id == ForecastAccuracyRecord.line_item_id)
            .filter(LineItem.is_target_bearing.isnot(False))
            .all()
        )
        detected.extend(
            detect_error_bias(
                records, min_cycles=min_cycles, max_candidates=max_candidates
            )
        )
    if "override_pattern" in selected:
        overrides = (
            db.query(Override)
            .join(LineItem, LineItem.id == Override.line_item_id)
            .filter(Override.status == "active")
            .filter(LineItem.is_target_bearing.isnot(False))
            .all()
        )
        detected.extend(
            detect_override_patterns(
                overrides, min_cycles=min_cycles, max_candidates=max_candidates
            )
        )

    now = datetime.now(timezone.utc)
    review_by = now + timedelta(days=CANDIDATE_REVIEW_DAYS)
    created = 0
    updated = 0
    skipped_active = 0
    for candidate in detected:
        open_candidate, active = _find_existing(db, candidate)
        if active is not None:
            skipped_active += 1
            continue
        if open_candidate is not None:
            open_candidate.statement = candidate["statement"]
            open_candidate.effect_size = candidate["effect_size"]
            open_candidate.evidence = candidate["evidence"]
            open_candidate.source = candidate["source"]
            open_candidate.proposed_at = now
            open_candidate.review_by = review_by
            updated += 1
            continue
        db.add(
            LearnedHeuristic(
                scope=candidate["scope"],
                line_item_id=candidate["line_item_id"],
                model_type=candidate["model_type"],
                horizon_bucket=candidate["horizon_bucket"],
                kind=candidate["kind"],
                statement=candidate["statement"],
                effect_size=candidate["effect_size"],
                evidence=candidate["evidence"],
                status="candidate",
                source=candidate["source"],
                proposed_at=now,
                review_by=review_by,
            )
        )
        created += 1

    db.flush()
    summary = {
        "detected": len(detected),
        "error_bias": sum(1 for c in detected if c["kind"] == "error_bias"),
        "override_pattern": sum(1 for c in detected if c["kind"] == "override_pattern"),
        "created": created,
        "updated": updated,
        "skipped_active": skipped_active,
        "min_cycles": min_cycles,
        "max_candidates": max_candidates,
        "candidates": detected,
    }
    record_audit(
        db,
        action="reflection.pass.run",
        entity_type="learned_heuristic",
        entity_id=None,
        actor_id=getattr(actor, "id", None),
        actor_username=getattr(actor, "username", None),
        details={k: v for k, v in summary.items() if k != "candidates"},
    )
    db.commit()
    return summary


def influences_selection(row: LearnedHeuristic) -> bool:
    """True when an active heuristic is allowed to influence model selection."""
    return row.status == "active" and (row.source or "") in INFLUENCING_SOURCES


def heuristic_summary(row: LearnedHeuristic) -> dict[str, Any]:
    """Compact, JSON-safe description for stamping into forecast metadata."""
    return {
        "id": row.id,
        "kind": row.kind,
        "scope": row.scope,
        "line_item_id": row.line_item_id,
        "model_type": row.model_type,
        "horizon_bucket": row.horizon_bucket,
        "source": row.source,
        "effect_size": row.effect_size,
        "statement": row.statement,
        "influences_selection": influences_selection(row),
    }


def _get_heuristic(db: Session, heuristic_id: int) -> LearnedHeuristic:
    row = (
        db.query(LearnedHeuristic)
        .filter(LearnedHeuristic.id == heuristic_id)
        .first()
    )
    if row is None:
        raise ValueError(f"Heuristic {heuristic_id} not found")
    return row


def _supersede_siblings(db: Session, row: LearnedHeuristic) -> list[int]:
    """Retire other active heuristics covering the same slice.

    Two active heuristics for the same (kind, scope, line item, category, model,
    horizon bucket) would contradict each other, so promoting one retires the
    rest of that signature.
    """
    siblings = (
        db.query(LearnedHeuristic)
        .filter(
            LearnedHeuristic.id != row.id,
            LearnedHeuristic.status == "active",
            LearnedHeuristic.kind == row.kind,
            LearnedHeuristic.scope == row.scope,
            LearnedHeuristic.line_item_id == row.line_item_id,
            LearnedHeuristic.category == row.category,
            LearnedHeuristic.model_type == row.model_type,
            LearnedHeuristic.horizon_bucket == row.horizon_bucket,
        )
        .all()
    )
    for sibling in siblings:
        sibling.status = "superseded"
    return [s.id for s in siblings]


def promote_heuristic(
    db: Session, heuristic_id: int, actor: User | None = None
) -> LearnedHeuristic:
    """Promote a candidate to ``active`` and retire conflicting active rows.

    Sets the transient ``influences_selection`` attribute on the returned row so
    callers can tell a consumable heuristic from a merely-visible one.
    """
    row = _get_heuristic(db, heuristic_id)
    if row.status not in ("candidate", "active"):
        raise ValueError(
            f"Heuristic {heuristic_id} is '{row.status}' — only candidate or "
            "active heuristics can be promoted"
        )

    row.status = "active"
    row.approved_by = getattr(actor, "id", None)
    superseded = _supersede_siblings(db, row)
    db.flush()

    consumable = influences_selection(row)
    record_audit(
        db,
        action="heuristic.promote",
        entity_type="learned_heuristic",
        entity_id=str(row.id),
        actor_id=getattr(actor, "id", None),
        actor_username=getattr(actor, "username", None),
        details={
            "kind": row.kind,
            "source": row.source,
            "line_item_id": row.line_item_id,
            "model_type": row.model_type,
            "horizon_bucket": row.horizon_bucket,
            "effect_size": row.effect_size,
            "superseded_ids": superseded,
            "influences_selection": consumable,
        },
    )
    db.commit()
    db.refresh(row)
    row.influences_selection = consumable
    row.superseded_ids = superseded
    return row


def reject_heuristic(
    db: Session, heuristic_id: int, actor: User | None = None
) -> LearnedHeuristic:
    """Mark a candidate or active heuristic ``rejected``."""
    row = _get_heuristic(db, heuristic_id)
    if row.status not in ("candidate", "active"):
        raise ValueError(
            f"Heuristic {heuristic_id} is '{row.status}' — only candidate or "
            "active heuristics can be rejected"
        )
    previous = row.status
    row.status = "rejected"
    row.approved_by = getattr(actor, "id", None)
    db.flush()

    record_audit(
        db,
        action="heuristic.reject",
        entity_type="learned_heuristic",
        entity_id=str(row.id),
        actor_id=getattr(actor, "id", None),
        actor_username=getattr(actor, "username", None),
        details={
            "kind": row.kind,
            "source": row.source,
            "line_item_id": row.line_item_id,
            "previous_status": previous,
        },
    )
    db.commit()
    db.refresh(row)
    row.influences_selection = False
    return row


def active_heuristics_for_line(
    db: Session,
    line_item_id: int,
    *,
    kind: str = "error_bias",
    consumable_only: bool = True,
) -> list[LearnedHeuristic]:
    """Active heuristics attached to one line item, newest proposal first.

    With ``consumable_only`` (the default) override-derived rows are filtered
    out, so the result is safe to feed into selection logic.
    """
    q = db.query(LearnedHeuristic).filter(
        LearnedHeuristic.line_item_id == line_item_id,
        LearnedHeuristic.status == "active",
    )
    if kind:
        q = q.filter(LearnedHeuristic.kind == kind)
    if consumable_only:
        q = q.filter(LearnedHeuristic.source.in_(tuple(INFLUENCING_SOURCES)))
    return q.order_by(
        LearnedHeuristic.proposed_at.desc(), LearnedHeuristic.id.desc()
    ).all()
