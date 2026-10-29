"""Custom KPI definitions (company-owned formulas) and their evaluation."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.auth import get_current_user
from app.database import get_db
from app.domain.kpi_formula import references
from app.models.kpi import KpiDefinition
from app.models.user import User
from app.services.kpis import (
    compile_kpi_formula,
    create_kpi,
    delete_kpi,
    evaluate_kpis,
    list_kpis,
    update_kpi,
)
from app.services.permissions import (
    can_access_business_unit,
    get_accessible_forecast_version,
    require_permission,
)

router = APIRouter(prefix="/kpis", tags=["kpis"])

DisplayFormat = Literal["number", "currency", "percent", "ratio"]


class KpiCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    formula: str = Field(min_length=1)
    display_format: DisplayFormat = "number"
    description: str | None = None
    business_unit_id: str | None = None


class KpiUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    formula: str | None = Field(default=None, min_length=1)
    display_format: DisplayFormat | None = None
    description: str | None = None


class FormulaCheck(BaseModel):
    formula: str
    business_unit_id: str | None = None


def _company(user: User, business_unit_id: str | None) -> str:
    """The named company if the caller can reach it, else their own; 404
    either way otherwise, so ids from other companies can't be probed."""
    bu_id = business_unit_id or user.business_unit_id
    if not bu_id or not can_access_business_unit(user, bu_id):
        raise HTTPException(404, "Business unit not found")
    return bu_id


def _dict(kpi: KpiDefinition) -> dict:
    return {
        "id": kpi.id,
        "business_unit_id": kpi.business_unit_id,
        "name": kpi.name,
        "formula": kpi.formula,
        "display_format": kpi.display_format,
        "description": kpi.description,
    }


def _owned(db: Session, kpi_id: str, user: User) -> KpiDefinition:
    kpi = db.query(KpiDefinition).filter(KpiDefinition.id == kpi_id).first()
    if kpi is None or not can_access_business_unit(user, kpi.business_unit_id):
        raise HTTPException(404, "KPI not found")
    return kpi


@router.get("")
async def get_kpis(
    business_unit_id: str | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    bu_id = _company(current_user, business_unit_id)
    return {"kpis": [_dict(k) for k in list_kpis(db, business_unit_id=bu_id)]}


@router.post("/validate")
async def validate_formula(
    body: FormulaCheck,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Check a formula against the company's chart of accounts without saving."""
    bu_id = _company(current_user, body.business_unit_id)
    try:
        node, resolved = compile_kpi_formula(db, business_unit_id=bu_id, formula=body.formula)
    except ValueError as e:
        return {"valid": False, "error": str(e)}
    return {
        "valid": True,
        "references": [
            {"reference": str(ref), "kind": resolved[ref][0]} for ref in references(node)
        ],
    }


@router.post("", status_code=201)
async def post_kpi(
    body: KpiCreate,
    current_user: User = Depends(require_permission("admin")),
    db: Session = Depends(get_db),
):
    bu_id = _company(current_user, body.business_unit_id)
    try:
        kpi = create_kpi(
            db,
            business_unit_id=bu_id,
            name=body.name,
            formula=body.formula,
            display_format=body.display_format,
            description=body.description,
            actor=current_user,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.commit()
    return _dict(kpi)


@router.put("/{kpi_id}")
async def put_kpi(
    kpi_id: str,
    body: KpiUpdate,
    current_user: User = Depends(require_permission("admin")),
    db: Session = Depends(get_db),
):
    kpi = _owned(db, kpi_id, current_user)
    try:
        update_kpi(db, kpi, changes=body.model_dump(exclude_unset=True), actor=current_user)
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.commit()
    return _dict(kpi)


@router.delete("/{kpi_id}", status_code=204)
async def remove_kpi(
    kpi_id: str,
    current_user: User = Depends(require_permission("admin")),
    db: Session = Depends(get_db),
):
    delete_kpi(db, _owned(db, kpi_id, current_user), actor=current_user)
    db.commit()


@router.get("/evaluate/{version_id}")
async def evaluate_for_version(
    version_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Every KPI of the version's company, evaluated against that version."""
    version = get_accessible_forecast_version(db, current_user, version_id)
    return {"version_id": version.id, "kpis": evaluate_kpis(db, version)}
