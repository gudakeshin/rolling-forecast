"""Executive landing, board pack export, budget bridge, driver drill-down."""

from __future__ import annotations


from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.api.auth import get_current_user
from app.models.user import User
from app.models.forecast import ForecastVersion, ForecastLineResult
from app.models.line_item import LineItem
from app.models.budget import BudgetVersion, BudgetLineItem
from app.models.actuals import ActualsRecord
from app.models.override import Override
from app.services.permissions import (
    allowed_line_item_ids,
    require_permission,
    scoped_line_items,
    user_can_view_line_item,
)
from app.services.board_pack import build_board_pack_pptx, build_board_pack_pdf
from app.services.audit import record_audit

router = APIRouter(prefix="/executive", tags=["executive"])


@router.get("/latest")
async def latest_published(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Read-only executive landing: latest published forecast + KPIs (no chat required)."""
    version = (
        db.query(ForecastVersion)
        .filter(ForecastVersion.status == "published")
        .order_by(ForecastVersion.published_at.desc().nullslast(), ForecastVersion.created_at.desc())
        .first()
    )
    if not version:
        # Fall back to approved, then newest
        version = (
            db.query(ForecastVersion)
            .filter(ForecastVersion.status.in_(["approved", "published", "in_review"]))
            .order_by(ForecastVersion.created_at.desc())
            .first()
        )
    if not version:
        return {"version": None, "message": "No published forecast available"}

    # Category rollups (BU-scoped)
    rows_q = (
        db.query(ForecastLineResult, LineItem)
        .join(LineItem, LineItem.id == ForecastLineResult.line_item_id)
        .filter(ForecastLineResult.version_id == version.id)
    )
    from app.services.permissions import line_item_scope_filter

    rows_q = line_item_scope_filter(rows_q, current_user, LineItem)
    rows = rows_q.all()
    by_cat: dict[str, float] = {}
    for r, li in rows:
        val = r.override_value if r.is_overridden else r.p50
        by_cat[li.category] = by_cat.get(li.category, 0.0) + float(val or 0)

    return {
        "version": {
            "id": version.id,
            "name": version.name,
            "status": version.status,
            "horizon_months": version.horizon_months,
            "published_at": version.published_at.isoformat() if version.published_at else None,
            "approved_at": version.approved_at.isoformat() if version.approved_at else None,
            "high_confidence_count": version.high_confidence_count,
            "medium_confidence_count": version.medium_confidence_count,
            "low_confidence_count": version.low_confidence_count,
            "override_count": version.override_count,
        },
        "kpis": {
            "category_totals": [{"category": k, "total": v} for k, v in sorted(by_cat.items())],
            "confidence": {
                "high": version.high_confidence_count,
                "medium": version.medium_confidence_count,
                "low": version.low_confidence_count,
            },
        },
    }


@router.get("/board-pack/{version_id}")
async def download_board_pack(
    version_id: str,
    format: str = Query("pptx", pattern="^(pptx|pdf)$"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    version = db.query(ForecastVersion).filter(ForecastVersion.id == version_id).first()
    if not version:
        raise HTTPException(404, "Forecast version not found")

    if format == "pptx":
        data = build_board_pack_pptx(db, version)
        media = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
        filename = f"{version.name}_board_pack.pptx"
    else:
        data = build_board_pack_pdf(db, version)
        media = "application/pdf"
        filename = f"{version.name}_board_pack.pdf"

    record_audit(
        db,
        action="executive.board_pack",
        entity_type="forecast_version",
        entity_id=version_id,
        actor_id=current_user.id,
        actor_username=current_user.username,
        details={"format": format},
        commit=True,
    )
    return Response(
        content=data,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/budget-bridge/{version_id}")
async def budget_bridge(
    version_id: str,
    budget_version_id: str | None = None,
    materiality_pct: float = Query(5.0, ge=0),
    page: int = Query(1, ge=1),
    page_size: int | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Compare forecast vs budget vs prior vs YTD actuals with materiality flags."""
    page_size = page_size or settings.panel_page_size
    version = db.query(ForecastVersion).filter(ForecastVersion.id == version_id).first()
    if not version:
        raise HTTPException(404, "Forecast version not found")

    budget = None
    if budget_version_id:
        budget = db.query(BudgetVersion).filter(BudgetVersion.id == budget_version_id).first()
    if not budget:
        budget = (
            db.query(BudgetVersion)
            .filter(BudgetVersion.status == "active")
            .order_by(BudgetVersion.fiscal_year.desc())
            .first()
        )

    prior = None
    if version.parent_version_id:
        prior = db.query(ForecastVersion).filter(ForecastVersion.id == version.parent_version_id).first()
    if not prior:
        prior = (
            db.query(ForecastVersion)
            .filter(
                ForecastVersion.id != version.id,
                ForecastVersion.status.in_(["published", "approved"]),
            )
            .order_by(ForecastVersion.created_at.desc())
            .first()
        )

    line_items = (
        scoped_line_items(db, current_user)
        .order_by(LineItem.display_order, LineItem.name)
        .all()
    )
    rows_out = []
    for li in line_items:
        fc_rows = (
            db.query(ForecastLineResult)
            .filter(
                ForecastLineResult.version_id == version.id,
                ForecastLineResult.line_item_id == li.id,
            )
            .all()
        )
        fc_total = sum(
            (r.override_value if r.is_overridden else r.p50) or 0 for r in fc_rows
        )

        budget_total = 0.0
        if budget:
            budget_total = sum(
                b.value
                for b in db.query(BudgetLineItem)
                .filter(
                    BudgetLineItem.budget_version_id == budget.id,
                    BudgetLineItem.line_item_id == li.id,
                )
                .all()
            )

        prior_total = 0.0
        if prior:
            prior_total = sum(
                (r.override_value if r.is_overridden else r.p50) or 0
                for r in db.query(ForecastLineResult)
                .filter(
                    ForecastLineResult.version_id == prior.id,
                    ForecastLineResult.line_item_id == li.id,
                )
                .all()
            )

        ytd_actuals = sum(
            a.value
            for a in db.query(ActualsRecord).filter(ActualsRecord.line_item_id == li.id).all()
        )

        var_budget = fc_total - budget_total
        var_prior = fc_total - prior_total
        var_budget_pct = (var_budget / abs(budget_total) * 100) if budget_total else None
        material = abs(var_budget_pct or 0) >= materiality_pct if budget_total else False

        rows_out.append({
            "line_item_id": li.id,
            "line_item": li.name,
            "category": li.category,
            "ytd_actuals": ytd_actuals,
            "current_forecast": fc_total,
            "prior_forecast": prior_total,
            "budget": budget_total,
            "variance_vs_prior": var_prior,
            "variance_vs_budget": var_budget,
            "variance_vs_budget_pct": var_budget_pct,
            "material": material,
        })

    total = len(rows_out)
    start = (page - 1) * page_size
    page_rows = rows_out[start : start + page_size]
    return {
        "version_id": version.id,
        "budget_version_id": budget.id if budget else None,
        "prior_version_id": prior.id if prior else None,
        "materiality_pct": materiality_pct,
        "total": total,
        "page": page,
        "page_size": page_size,
        "rows": page_rows,
    }


@router.get("/driver-drilldown/{version_id}")
async def driver_drilldown(
    version_id: str,
    line_item_id: int | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Volume / price / mix / FX style driver breakdown from overrides + driver inputs."""
    from app.models.driver_input import DriverInput

    version = db.query(ForecastVersion).filter(ForecastVersion.id == version_id).first()
    if not version:
        raise HTTPException(404, "Forecast version not found")

    allowed_ids = allowed_line_item_ids(db, current_user)
    overrides = db.query(Override).filter(
        Override.version_id == version_id, Override.status == "active"
    )
    if line_item_id:
        overrides = overrides.filter(Override.line_item_id == line_item_id)
    if allowed_ids is not None:
        overrides = overrides.filter(Override.line_item_id.in_(allowed_ids))
    overrides = overrides.all()

    drivers = db.query(DriverInput).filter(DriverInput.version_id == version_id).all()
    # Driver forms are BU-tagged; restrict to caller's BU when scoped
    if not getattr(current_user.role, "can_view_all_bus", False) and not (
        current_user.role and current_user.role.can_admin
    ):
        bu = current_user.business_unit
        drivers = [d for d in drivers if not d.business_unit or d.business_unit == bu]

    breakdown = []
    for o in overrides:
        li = db.query(LineItem).filter(LineItem.id == o.line_item_id).first()
        if not user_can_view_line_item(current_user, li):
            continue
        delta = o.override_value - o.original_model_value
        reason_l = (o.reason or "").lower()
        # Heuristic attribution from reason text
        attrs = {"volume": 0.0, "price": 0.0, "mix": 0.0, "fx": 0.0, "other": 0.0}
        if "volume" in reason_l or "units" in reason_l:
            attrs["volume"] = delta
        elif "price" in reason_l or "asp" in reason_l or "rate" in reason_l:
            attrs["price"] = delta
        elif "mix" in reason_l:
            attrs["mix"] = delta
        elif "fx" in reason_l or "currency" in reason_l or "forex" in reason_l:
            attrs["fx"] = delta
        else:
            attrs["other"] = delta
        breakdown.append({
            "line_item_id": o.line_item_id,
            "line_item": li.name if li else str(o.line_item_id),
            "period": o.period,
            "model_value": o.original_model_value,
            "override_value": o.override_value,
            "delta": delta,
            "reason": o.reason,
            "drivers": attrs,
        })

    driver_fields = []
    for di in drivers:
        for field, payload in (di.values or {}).items():
            driver_fields.append({
                "business_unit": di.business_unit,
                "field": field,
                "value": payload.get("value") if isinstance(payload, dict) else payload,
                "source": payload.get("source") if isinstance(payload, dict) else None,
            })

    return {
        "version_id": version_id,
        "override_breakdown": breakdown,
        "submitted_driver_fields": driver_fields,
    }


class DistributeRequest(BaseModel):
    version_id: str
    recipients: list[EmailStr]
    format: str = "pdf"
    subject: str | None = None
    message: str | None = None


@router.post("/distribute")
async def distribute_pack(
    body: DistributeRequest,
    current_user: User = Depends(require_permission("publish")),
    db: Session = Depends(get_db),
):
    """Email board pack to recipients (SMTP). Records audit even if SMTP not configured."""
    version = db.query(ForecastVersion).filter(ForecastVersion.id == body.version_id).first()
    if not version:
        raise HTTPException(404, "Forecast version not found")

    if body.format == "pptx":
        data = build_board_pack_pptx(db, version)
        filename = f"{version.name}_board_pack.pptx"
    else:
        data = build_board_pack_pdf(db, version)
        filename = f"{version.name}_board_pack.pdf"

    sent = False
    error = None
    if settings.smtp_host:
        try:
            import smtplib
            from email.message import EmailMessage

            msg = EmailMessage()
            msg["Subject"] = body.subject or f"Forecast Board Pack: {version.name}"
            msg["From"] = settings.distribution_from_email
            msg["To"] = ", ".join(body.recipients)
            msg.set_content(body.message or f"Attached is the board pack for {version.name}.")
            maintype = "application"
            subtype = "pdf" if body.format == "pdf" else "vnd.openxmlformats-officedocument.presentationml.presentation"
            msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=filename)

            with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as smtp:
                smtp.starttls()
                if settings.smtp_user:
                    smtp.login(settings.smtp_user, settings.smtp_password)
                smtp.send_message(msg)
            sent = True
        except Exception as e:
            error = str(e)
    else:
        error = "SMTP not configured; pack generated but not emailed"

    record_audit(
        db,
        action="executive.distribute",
        entity_type="forecast_version",
        entity_id=body.version_id,
        actor_id=current_user.id,
        actor_username=current_user.username,
        details={"recipients": body.recipients, "sent": sent, "error": error},
        commit=True,
    )
    return {"success": sent, "filename": filename, "error": error, "bytes": len(data)}
