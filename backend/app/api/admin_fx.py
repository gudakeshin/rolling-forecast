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
    current_user: User = Depends(require_permission("admin")),
    db: Session = Depends(get_db),
):
    return {"reporting_currency": get_reporting_currency(db)}


@router.put("/settings/reporting_currency")
async def put_currency(
    body: ReportingCurrencyBody,
    current_user: User = Depends(require_permission("admin")),
    db: Session = Depends(get_db),
):
    set_reporting_currency(db, body.currency)
    record_audit(
        db,
        action="admin.fx_reporting_currency",
        entity_type="system_setting",
        entity_id="reporting_currency",
        actor_id=current_user.id,
        actor_username=current_user.username,
        details={"currency": body.currency.upper()},
    )
    db.commit()
    return {"reporting_currency": body.currency.upper()}


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
