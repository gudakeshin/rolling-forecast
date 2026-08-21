"""Version immutability helpers — overrides create a new draft child version."""

from __future__ import annotations

import copy
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.forecast import ForecastVersion, ForecastLineResult
from app.models.override import Override

logger = logging.getLogger(__name__)


def clone_version_for_edit(
    db: Session,
    source: ForecastVersion,
    *,
    created_by: str | None,
    label_suffix: str = "override",
) -> ForecastVersion:
    """
    Create an immutable-preserving child version by deep-copying line results.
    The source version is left unchanged (SOX-friendly snapshot semantics).
    """
    new_name = f"{source.name}-{label_suffix}-{datetime.now(timezone.utc).strftime('%H%M%S')}"
    child = ForecastVersion(
        id=str(uuid.uuid4()),
        name=new_name,
        label=f"{source.label or source.name} ({label_suffix})",
        status="draft",
        version_type="ad_hoc",
        parent_version_id=source.id,
        actuals_dataset_id=source.actuals_dataset_id,
        actuals_hash=source.actuals_hash,
        input_hash=source.input_hash,
        horizon_months=source.horizon_months,
        base_period=source.base_period,
        model_versions=copy.deepcopy(source.model_versions) if source.model_versions else None,
        random_seed=source.random_seed,
        created_by=created_by,
        total_line_items=source.total_line_items,
        high_confidence_count=source.high_confidence_count,
        medium_confidence_count=source.medium_confidence_count,
        low_confidence_count=source.low_confidence_count,
        override_count=source.override_count,
        notes=f"Cloned from {source.id} for {label_suffix}",
    )
    db.add(child)
    db.flush()

    results = (
        db.query(ForecastLineResult)
        .filter(ForecastLineResult.version_id == source.id)
        .all()
    )
    for r in results:
        db.add(
            ForecastLineResult(
                id=str(uuid.uuid4()),
                version_id=child.id,
                line_item_id=r.line_item_id,
                period=r.period,
                p10=r.p10,
                p50=r.p50,
                p90=r.p90,
                confidence_score=r.confidence_score,
                confidence_level=r.confidence_level,
                model_type=r.model_type,
                is_overridden=r.is_overridden,
                override_value=r.override_value,
                is_calculated=getattr(r, "is_calculated", False),
                review_status=None,
                review_comment=None,
                reviewed_by=None,
                reviewed_at=None,
                ai_recommendation=getattr(r, "ai_recommendation", None),
                ai_reasoning=getattr(r, "ai_reasoning", None),
                ai_risk_score=getattr(r, "ai_risk_score", None),
            )
        )

    # Copy active overrides as historical lineage on the new version
    overrides = (
        db.query(Override)
        .filter(Override.version_id == source.id, Override.status == "active")
        .all()
    )
    for o in overrides:
        db.add(
            Override(
                version_id=child.id,
                line_item_id=o.line_item_id,
                period=o.period,
                original_model_value=o.original_model_value,
                override_value=o.override_value,
                reason=o.reason,
                carry_forward=o.carry_forward,
                user_id=o.user_id,
                status="active",
                downstream_recalc_count=o.downstream_recalc_count,
            )
        )

    db.flush()
    logger.info("Cloned version %s -> %s for immutable edit", source.id, child.id)
    return child


def ensure_editable_version(
    db: Session,
    version: ForecastVersion,
    *,
    user_id: str | None,
    clone_if_locked: bool = True,
) -> tuple[ForecastVersion, bool]:
    """
    Return a version that can be mutated.
    If status is approved/published/archived, clone a new draft (when clone_if_locked).
    Returns (version, was_cloned).
    """
    if version.status in ("draft", "in_review"):
        return version, False
    if not clone_if_locked:
        raise ValueError(
            f"Cannot modify a '{version.status}' forecast. Clone or create a new version."
        )
    child = clone_version_for_edit(db, version, created_by=user_id)
    return child, True
