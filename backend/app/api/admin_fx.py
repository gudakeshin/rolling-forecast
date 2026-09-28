"""Admin CRUD for FX rates and reporting currency."""

from __future__ import annotations

import csv
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.fx import FxRate
from app.models.user import User
from app.services.audit import record_audit
from app.services.fx import get_reporting_currency, set_reporting_currency
from app.services.permissions import require_permission
from app.services.upload_safety import save_upload

router = APIRouter(prefix="/admin/fx", tags=["admin-fx"])


class FxRateCreate(BaseModel):
    from_currency: str = Field(..., min_length=3, max_length=3)
    to_currency: str = Field(..., min_length=3, max_length=3)
    period: str = Field(..., pattern=r"^\d{4}-\d{2}$")
    rate: float = Field(..., gt=0)
    rate_type: str = Field(default="average", pattern="^(average|spot|budget)$")


class FxRateUpdate(BaseModel):
    rate: float | None = Field(default=None, gt=0)
    rate_type: str | None = Field(default=None, pattern="^(average|spot|budget)$")


class ReportingCurrencyBody(BaseModel):
    currency: str = Field(..., min_length=3, max_length=3)
    business_unit_id: str | None = None


def _resolve_settings_business_unit(
    db: Session, current_user: User, business_unit_id: str | None
) -> str | None:
    """Which company's setting a caller is viewing/editing here.

    An explicit business_unit_id wins (only a cross-BU caller may pass one
    that isn't their own). Otherwise a company-scoped admin sees/edits their
    own company's override; a cross-BU admin with none specified sees/edits
    the global default, matching this endpoint's pre-scoping behavior.
    """
    from app.services.permissions import can_access_business_unit, can_view_all_bus

    if business_unit_id:
        if not can_access_business_unit(current_user, business_unit_id):
            raise HTTPException(404, "Business unit not found")
        return business_unit_id
    if not can_view_all_bus(current_user) and current_user.business_unit_id:
        return current_user.business_unit_id
    return None


def _public(row: FxRate) -> dict:
    return {
        "id": row.id,
        "from_currency": row.from_currency,
        "to_currency": row.to_currency,
        "period": row.period,
        "rate": row.rate,
        "rate_type": row.rate_type,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


@router.get("/settings/reporting_currency")
async def get_currency(
    business_unit_id: str | None = None,
    current_user: User = Depends(require_permission("admin")),
    db: Session = Depends(get_db),
):
    bu_id = _resolve_settings_business_unit(db, current_user, business_unit_id)
    return {
        "reporting_currency": get_reporting_currency(db, business_unit_id=bu_id),
        "business_unit_id": bu_id,
    }


@router.put("/settings/reporting_currency")
async def put_currency(
    body: ReportingCurrencyBody,
    current_user: User = Depends(require_permission("admin")),
    db: Session = Depends(get_db),
):
    bu_id = _resolve_settings_business_unit(db, current_user, body.business_unit_id)
    set_reporting_currency(db, body.currency, business_unit_id=bu_id)
    record_audit(
        db,
        action="admin.fx_reporting_currency",
        entity_type="system_setting",
        entity_id="reporting_currency",
        actor_id=current_user.id,
        actor_username=current_user.username,
        details={"currency": body.currency.upper(), "business_unit_id": bu_id},
    )
    db.commit()
    return {"reporting_currency": body.currency.upper(), "business_unit_id": bu_id}


@router.get("/rates")
async def list_rates(
    from_currency: str | None = None,
    to_currency: str | None = None,
    period: str | None = None,
    current_user: User = Depends(require_permission("admin")),
    db: Session = Depends(get_db),
):
    q = db.query(FxRate)
    if from_currency:
        q = q.filter(FxRate.from_currency == from_currency.upper())
    if to_currency:
        q = q.filter(FxRate.to_currency == to_currency.upper())
    if period:
        q = q.filter(FxRate.period == period)
    rows = q.order_by(FxRate.period.desc(), FxRate.from_currency).limit(500).all()
    return [_public(r) for r in rows]


@router.post("/rates")
async def create_rate(
    body: FxRateCreate,
    current_user: User = Depends(require_permission("admin")),
    db: Session = Depends(get_db),
):
    existing = (
        db.query(FxRate)
        .filter(
            FxRate.from_currency == body.from_currency.upper(),
            FxRate.to_currency == body.to_currency.upper(),
            FxRate.period == body.period,
            FxRate.rate_type == body.rate_type,
        )
        .first()
    )
    if existing:
        raise HTTPException(409, "FX rate already exists for this pair/period/type")
    row = FxRate(
        from_currency=body.from_currency.upper(),
        to_currency=body.to_currency.upper(),
        period=body.period,
        rate=body.rate,
        rate_type=body.rate_type,
    )
    db.add(row)
    record_audit(
        db,
        action="admin.fx_rate_create",
        entity_type="fx_rate",
        entity_id=None,
        actor_id=current_user.id,
        actor_username=current_user.username,
        details={
            "from": row.from_currency,
            "to": row.to_currency,
            "period": row.period,
            "rate": row.rate,
        },
    )
    db.commit()
    db.refresh(row)
    return _public(row)


@router.patch("/rates/{rate_id}")
async def update_rate(
    rate_id: str,
    body: FxRateUpdate,
    current_user: User = Depends(require_permission("admin")),
    db: Session = Depends(get_db),
):
    row = db.query(FxRate).filter(FxRate.id == rate_id).first()
    if not row:
        raise HTTPException(404, "FX rate not found")
    if body.rate is not None:
        row.rate = body.rate
    if body.rate_type is not None:
        row.rate_type = body.rate_type
    record_audit(
        db,
        action="admin.fx_rate_update",
        entity_type="fx_rate",
        entity_id=row.id,
        actor_id=current_user.id,
        actor_username=current_user.username,
        details={"fields": list(body.model_dump(exclude_none=True).keys())},
    )
    db.commit()
    db.refresh(row)
    return _public(row)


@router.delete("/rates/{rate_id}")
async def delete_rate(
    rate_id: str,
    current_user: User = Depends(require_permission("admin")),
    db: Session = Depends(get_db),
):
    row = db.query(FxRate).filter(FxRate.id == rate_id).first()
    if not row:
        raise HTTPException(404, "FX rate not found")
    db.delete(row)
    record_audit(
        db,
        action="admin.fx_rate_delete",
        entity_type="fx_rate",
        entity_id=rate_id,
        actor_id=current_user.id,
        actor_username=current_user.username,
        details={},
    )
    db.commit()
    return {"ok": True}


@router.post("/rates/upload")
async def upload_rates_csv(
    file: UploadFile = File(...),
    current_user: User = Depends(require_permission("admin")),
    db: Session = Depends(get_db),
):
    """CSV columns: from_currency,to_currency,period,rate[,rate_type]."""
    saved = await save_upload(file, allowed_extensions={".csv", ".txt"})
    created = 0
    updated = 0
    errors: list[str] = []
    try:
        with open(saved["stored_path"], newline="", encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh)
            required = {"from_currency", "to_currency", "period", "rate"}
            if not reader.fieldnames or not required.issubset({c.lower() for c in reader.fieldnames}):
                raise HTTPException(
                    400,
                    "CSV must include columns: from_currency,to_currency,period,rate",
                )
            # Normalize field names
            for i, raw in enumerate(reader, start=2):
                row = { (k or "").strip().lower(): (v or "").strip() for k, v in raw.items() }
                try:
                    frm = row["from_currency"].upper()
                    to = row["to_currency"].upper()
                    period = row["period"]
                    rate = float(row["rate"])
                    rate_type = (row.get("rate_type") or "average").lower()
                    if rate <= 0:
                        raise ValueError("rate must be > 0")
                    existing = (
                        db.query(FxRate)
                        .filter(
                            FxRate.from_currency == frm,
                            FxRate.to_currency == to,
                            FxRate.period == period,
                            FxRate.rate_type == rate_type,
                        )
                        .first()
                    )
                    if existing:
                        existing.rate = rate
                        updated += 1
                    else:
                        db.add(
                            FxRate(
                                from_currency=frm,
                                to_currency=to,
                                period=period,
                                rate=rate,
                                rate_type=rate_type,
                            )
                        )
                        created += 1
                except Exception as e:
                    errors.append(f"row {i}: {e}")
        record_audit(
            db,
            action="admin.fx_rate_upload",
            entity_type="fx_rate",
            entity_id=None,
            actor_id=current_user.id,
            actor_username=current_user.username,
            details={"created": created, "updated": updated, "errors": len(errors)},
        )
        db.commit()
    finally:
        try:
            import os
            os.unlink(saved["stored_path"])
        except OSError:
            pass

    return {
        "created": created,
        "updated": updated,
        "errors": errors[:50],
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
    }


# ── Fiscal calendar ──────────────────────────────────────────────


class FiscalCalendarBody(BaseModel):
    calendar_type: str = Field(..., pattern="^(gregorian_month|fiscal_445)$")
    fiscal_year_start_month: int = Field(default=2, ge=1, le=12)
    week_start: int = Field(default=6, ge=0, le=6)
    business_unit_id: str | None = None


@router.get("/settings/fiscal_calendar")
async def get_fiscal_calendar(
    business_unit_id: str | None = None,
    current_user: User = Depends(require_permission("admin")),
    db: Session = Depends(get_db),
):
    from app.services.period_calendar import get_calendar_config

    bu_id = _resolve_settings_business_unit(db, current_user, business_unit_id)
    cfg = get_calendar_config(db, business_unit_id=bu_id)
    return {
        "calendar_type": cfg.calendar_type.value,
        "fiscal_year_start_month": cfg.fiscal_year_start_month,
        "week_start": cfg.week_start,
        "business_unit_id": bu_id,
    }


@router.put("/settings/fiscal_calendar")
async def put_fiscal_calendar(
    body: FiscalCalendarBody,
    current_user: User = Depends(require_permission("admin")),
    db: Session = Depends(get_db),
):
    from app.services.period_calendar import (
        CalendarType,
        FiscalCalendarConfig,
        set_calendar_config,
    )

    bu_id = _resolve_settings_business_unit(db, current_user, body.business_unit_id)
    cfg = FiscalCalendarConfig(
        calendar_type=CalendarType(body.calendar_type),
        fiscal_year_start_month=body.fiscal_year_start_month,
        week_start=body.week_start,
    )
    set_calendar_config(db, cfg, business_unit_id=bu_id)
    record_audit(
        db,
        action="admin.fiscal_calendar",
        entity_type="system_setting",
        entity_id="fiscal_calendar",
        actor_id=current_user.id,
        actor_username=current_user.username,
        details={
            "calendar_type": cfg.calendar_type.value,
            "fiscal_year_start_month": cfg.fiscal_year_start_month,
            "week_start": cfg.week_start,
            "business_unit_id": bu_id,
        },
    )
    db.commit()
    return {
        "calendar_type": cfg.calendar_type.value,
        "fiscal_year_start_month": cfg.fiscal_year_start_month,
        "week_start": cfg.week_start,
        "business_unit_id": bu_id,
    }
