"""Driver input forms: field definitions and submission."""


import logging
from typing import Any

from fastapi import Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import false
from sqlalchemy.orm import Session

from app.api.auth import get_current_user
from app.database import get_db
from app.models.actuals import ActualsRecord
from app.models.forecast import ForecastLineResult
from app.models.line_item import LineItem
from app.models.user import User
from app.schemas.forecast import PanelDataResponse
from app.services.permissions import (
    get_accessible_forecast_version,
    require_permission,
    scoped_line_items,
)

# Scoring lives in services.confidence; remediation stays on the skill module.

logger = logging.getLogger(__name__)


from app.api.dashboard._common import router

# ──────────────────────────────────────────────────
# Driver Input Form Data
# ──────────────────────────────────────────────────

@router.get("/driver-inputs/{version_id}", response_model=PanelDataResponse)
async def get_driver_inputs(
    version_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get driver input form data with model-suggested values."""
    from app.models.driver_input import DriverFormConfig, DriverInput
    from app.services.permissions import can_view_all_bus

    version = get_accessible_forecast_version(db, current_user, version_id)

    # Get existing driver inputs, scoped to the caller's company -- there is
    # no "shared" bucket, so a caller with no business_unit_id assigned sees
    # none rather than every company's submissions.
    inputs_q = db.query(DriverInput).filter(DriverInput.version_id == version_id)
    form_configs_q = db.query(DriverFormConfig).filter(DriverFormConfig.is_active == True)
    if not can_view_all_bus(current_user):
        if current_user.business_unit_id:
            inputs_q = inputs_q.filter(DriverInput.business_unit_id == current_user.business_unit_id)
            form_configs_q = form_configs_q.filter(
                DriverFormConfig.business_unit_id == current_user.business_unit_id
            )
        else:
            inputs_q = inputs_q.filter(false())
            form_configs_q = form_configs_q.filter(false())
    inputs = inputs_q.order_by(DriverInput.business_unit, DriverInput.submitted_at.desc()).all()

    items = []
    for di in inputs:
        items.append({
            "id": di.id,
            "business_unit": di.business_unit,
            "values": di.values,
            "status": di.status,
            "is_late": di.is_late,
            "submitted_at": di.submitted_at.isoformat() if di.submitted_at else None,
            "reviewed_by": di.reviewed_by,
            "review_comments": di.review_comments,
        })

    # Get form configs for driver input structure
    form_configs = form_configs_q.all()
    forms = [{
        "id": fc.id,
        "business_unit": fc.business_unit,
        "name": fc.name,
        "description": fc.description,
        "fields_schema": fc.fields_schema,
        "soft_deadline_days": fc.soft_deadline_days,
        "hard_deadline_days": fc.hard_deadline_days,
    } for fc in form_configs]

    # Get line items available for driver input
    line_items = (
        scoped_line_items(db, current_user, LineItem.is_calculated == False)  # noqa: E712
        .order_by(LineItem.category, LineItem.display_order)
        .all()
    )
    li_ids = [li.id for li in line_items]

    # Batch first-period forecast + latest actual (avoid 2 queries per line item)
    first_flr_by_li: dict[int, ForecastLineResult] = {}
    last_actual_by_li: dict[int, ActualsRecord] = {}
    if li_ids:
        for flr in (
            db.query(ForecastLineResult)
            .filter(
                ForecastLineResult.version_id == version_id,
                ForecastLineResult.line_item_id.in_(li_ids),
            )
            .order_by(ForecastLineResult.period)
            .all()
        ):
            if flr.line_item_id not in first_flr_by_li:
                first_flr_by_li[flr.line_item_id] = flr
        for act in (
            db.query(ActualsRecord)
            .filter(ActualsRecord.line_item_id.in_(li_ids))
            .order_by(ActualsRecord.period.desc())
            .all()
        ):
            if act.line_item_id not in last_actual_by_li:
                last_actual_by_li[act.line_item_id] = act

    available_items = []
    for li in line_items:
        suggestion = first_flr_by_li.get(li.id)
        prior = last_actual_by_li.get(li.id)
        available_items.append({
            "id": li.id,
            "name": li.name,
            "category": li.category,
            "account_code": li.account_code,
            "model_suggested_value": suggestion.p50 if suggestion else None,
            "confidence": suggestion.confidence_score if suggestion else None,
            "last_actual": prior.value if prior else None,
        })

    return PanelDataResponse(
        panel_type="driver_inputs",
        title=f"Driver Inputs: {version.name}",
        data={
            "version": {"id": version.id, "name": version.name, "status": version.status},
            "inputs": items,
            "form_configs": forms,
            "available_line_items": available_items,
            "total_count": len(items),
        },
    )


class DriverSubmitRequest(BaseModel):
    version_id: str
    values: dict[str, Any]  # {line_item_id: {value, source}} or field_name keyed
    notes: str | None = None
    form_config_id: str | None = None
    business_unit: str | None = None


@router.post("/driver-inputs/submit")
async def submit_driver_inputs(
    request: DriverSubmitRequest,
    current_user: User = Depends(require_permission("input")),
    db: Session = Depends(get_db),
):
    """Submit BU driver assumptions for a forecast version."""
    from app.models.driver_input import DriverFormConfig
    from app.services.business_units import get_or_create_business_unit
    from app.services.driver_submission import apply_driver_submission
    from app.services.permissions import can_view_all_bus

    version = get_accessible_forecast_version(db, current_user, request.version_id)
    if version.status not in ("draft", "in_review"):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot submit drivers for a '{version.status}' forecast",
        )

    bu = request.business_unit or current_user.business_unit or "Default"
    bu_row = get_or_create_business_unit(db, bu)
    bu_id = bu_row.id if bu_row else None
    # A caller may only submit for a business_unit other than their own if
    # they can see across all of them -- otherwise `request.business_unit`
    # would let any "input"-permission user attach values to another
    # company's driver form. See app/api/drivers.py's create_driver_endpoint
    # for the same guard on driver creation.
    if request.business_unit and not can_view_all_bus(current_user):
        if bu_id is None or bu_id != current_user.business_unit_id:
            raise HTTPException(403, "Cannot submit driver inputs for another business unit")

    form = None
    if request.form_config_id:
        form = db.query(DriverFormConfig).filter(DriverFormConfig.id == request.form_config_id).first()
        if form and not can_view_all_bus(current_user) and form.business_unit_id != current_user.business_unit_id:
            raise HTTPException(404, "Driver form not found")
    if not form:
        form = (
            db.query(DriverFormConfig)
            .filter(DriverFormConfig.is_active == True, DriverFormConfig.business_unit_id == bu_id)
            .first()
        )
    if not form:
        # Auto-create a default form for this BU from submitted keys
        fields = []
        for key, payload in (request.values or {}).items():
            if key.startswith("_"):
                continue
            fields.append({
                "name": str(key),
                "label": str(key),
                "type": "number",
                "line_item_id": int(key) if str(key).isdigit() else None,
            })
        form = DriverFormConfig(
            business_unit=bu,
            business_unit_id=bu_id,
            name=f"{bu} Driver Form",
            description="Auto-generated driver form",
            fields_schema={"fields": fields or [{"name": "value", "label": "Value", "type": "number"}]},
            is_active=True,
        )
        db.add(form)
        db.flush()

    try:
        result = apply_driver_submission(
            db,
            version_id=version.id,
            form=form,
            values=request.values or {},
            user_id=current_user.id,
            business_unit=bu,
            notes=request.notes,
            apply_overrides=True,
            actor_username=current_user.username,
            audit=True,
            commit=True,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    di = result.driver_input
    return {
        "success": True,
        "id": di.id,
        "status": di.status,
        "overrides_applied": result.overrides_applied,
        "message": (
            f"Submitted {len(result.enriched_values)} driver value(s) for {bu}"
            + (f", {result.overrides_applied} applied as overrides" if result.overrides_applied else "")
        ),
    }


