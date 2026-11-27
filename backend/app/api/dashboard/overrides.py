"""Inline override from the forecast table."""


import logging
from datetime import datetime, timezone

from fastapi import Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.forecast import ForecastLineResult
from app.models.line_item import LineItem
from app.models.override import Override
from app.models.user import User
from app.services.permissions import (
    get_accessible_forecast_version,
    require_permission,
)

# Scoring lives in services.confidence; remediation stays on the skill module.

logger = logging.getLogger(__name__)


from app.api.dashboard._common import router

# ──────────────────────────────────────────────────
# Inline Override (lightweight, from forecast table)
# ──────────────────────────────────────────────────


class InlineOverrideRequest(BaseModel):
    """Quick override from the forecast table — applies to all periods of a line item."""
    result_id: str          # ForecastLineResult.id (representative row)
    new_value: float        # The user's updated value
    reason: str             # Why (min 10 chars enforced in frontend)
    apply_to: str = "all"   # "all" = all periods, "single" = only matching period


@router.post("/inline-override")
async def inline_override(
    request: InlineOverrideRequest,
    current_user: User = Depends(require_permission("override")),
    db: Session = Depends(get_db),
):
    """Apply a quick override from the forecast table.

    Creates Override records, updates ForecastLineResult, and triggers
    downstream DAG recalculation.
    """
    import uuid

    from app.services.audit import record_audit

    result = db.query(ForecastLineResult).filter(
        ForecastLineResult.id == request.result_id
    ).first()
    if not result:
        raise HTTPException(status_code=404, detail="Forecast line result not found")

    version = get_accessible_forecast_version(db, current_user, result.version_id)

    # Determine which results to override
    if request.apply_to == "all":
        targets = (
            db.query(ForecastLineResult)
            .filter(
                ForecastLineResult.version_id == result.version_id,
                ForecastLineResult.line_item_id == result.line_item_id,
            )
            .all()
        )
    else:
        targets = [result]

    now = datetime.now(timezone.utc)
    overrides_created = 0

    for t in targets:
        # Check for existing active override and supersede it
        existing = (
            db.query(Override)
            .filter(
                Override.version_id == t.version_id,
                Override.line_item_id == t.line_item_id,
                Override.period == t.period,
                Override.status == "active",
            )
            .first()
        )
        if existing:
            existing.status = "superseded"

        # Create new override record
        override = Override(
            id=str(uuid.uuid4()),
            version_id=t.version_id,
            line_item_id=t.line_item_id,
            period=t.period,
            original_model_value=t.p50,
            override_value=request.new_value,
            reason=request.reason,
            status="active",
            carry_forward=True,
            user_id=current_user.id,
            created_at=now,
        )
        db.add(override)

        # Update forecast result
        t.is_overridden = True
        t.override_value = request.new_value
        overrides_created += 1

    # Recalculate downstream dependents (p10/p90) + MinT reconciliation
    downstream_count = 0
    try:
        from app.services.overrides import recalculate_and_reconcile

        downstream_count = recalculate_and_reconcile(
            db,
            result.version_id,
            [result.line_item_id],
        )
    except Exception as e:
        logger.warning(f"DAG recalculation failed (non-fatal): {e}")

    # Update version override count
    version.override_count = (
        db.query(ForecastLineResult)
        .filter(
            ForecastLineResult.version_id == result.version_id,
            ForecastLineResult.is_overridden == True,  # noqa: E712
        )
        .count()
    )

    record_audit(
        db,
        action="override.inline",
        entity_type="forecast_line_result",
        entity_id=request.result_id,
        actor_id=current_user.id,
        actor_username=current_user.username,
        details={
            "version_id": result.version_id,
            "new_value": request.new_value,
            "overrides_created": overrides_created,
            "downstream_recalculated": downstream_count,
        },
    )
    db.commit()

    li = db.query(LineItem).filter(LineItem.id == result.line_item_id).first()
    li_name = li.name if li else "Unknown"

    return {
        "success": True,
        "message": (
            f"Override applied to {li_name}: ${request.new_value:,.0f} "
            f"({overrides_created} period(s), {downstream_count} downstream recalculated)"
        ),
        "overrides_created": overrides_created,
        "downstream_recalculated": downstream_count,
        "line_item_name": li_name,
    }


