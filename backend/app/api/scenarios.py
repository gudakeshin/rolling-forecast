"""Scenario APIs (Phase 7 what-if)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
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
    # links: push shocks through link coefficients (fast). foundation:
    # re-forecast linked lines with Chronos-2 under the shocked driver path.
    method: str = Field(default="links", pattern="^(links|foundation)$")


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
            method=body.method,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return result
