"""Scenario APIs (Phase 7 what-if)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.forecast import ForecastVersion
from app.models.user import User
from app.services.audit import record_audit
from app.services.permissions import require_permission
from app.services.scenario_what_if import DriverShock, create_what_if_scenario

router = APIRouter(prefix="/scenarios", tags=["scenarios"])


class DriverShockIn(BaseModel):
    driver_id: int
    mode: str = Field(pattern="^(pct|absolute|replace)$")
    value: float | None = None
    series: dict[str, float] | None = None


class WhatIfRequest(BaseModel):
    base_version_id: str
    scenario_label: str
    shocks: list[DriverShockIn]


@router.post("/what-if")
async def create_what_if(
    body: WhatIfRequest,
    current_user: User = Depends(require_permission("generate")),
    db: Session = Depends(get_db),
):
    """Create scenario version by applying driver shocks to linked line items."""
    try:
        result = create_what_if_scenario(
            db,
            base_version_id=body.base_version_id,
            scenario_label=body.scenario_label.strip() or "scenario",
            shocks=[
                DriverShock(
                    driver_id=s.driver_id,
                    mode=s.mode,
                    value=s.value,
                    series=s.series,
                )
                for s in body.shocks
            ],
            actor=current_user,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return result


@router.delete("/{version_id}")
async def delete_what_if_scenario(
    version_id: str,
    current_user: User = Depends(require_permission("generate")),
    db: Session = Depends(get_db),
):
    """Archive a what-if scenario version so it drops out of the version picker.

    Restricted to version_type == "scenario" — real forecast versions (scheduled,
    ad_hoc, branch) carry approvals, publications, and accuracy history and are
    never deletable through this route.
    """
    version = db.query(ForecastVersion).filter(ForecastVersion.id == version_id).first()
    if not version:
        raise HTTPException(404, "Scenario version not found")
    if version.version_type != "scenario":
        raise HTTPException(400, "Only what-if scenario versions can be deleted")
    if version.status == "archived":
        return {"id": version.id, "status": "archived"}

    version.status = "archived"
    record_audit(
        db,
        action="scenario.what_if.archive",
        entity_type="forecast_version",
        entity_id=version.id,
        actor_id=current_user.id,
        actor_username=current_user.username,
        details={"scenario_label": version.label or version.name},
    )
    db.commit()
    return {"id": version.id, "status": "archived"}
