"""Executive landing, board pack export, budget bridge, driver drill-down."""

from __future__ import annotations


from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, EmailStr
from sqlalchemy import case, func
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
    attribute: bool = Query(False, description="Attach Phase 6a variance attribution"),
    convention: str = Query("volume_first"),
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
    li_ids = [li.id for li in line_items]

    # Batched SUM aggregates — one query per source instead of 4×N
    effective_p50 = case(
        (ForecastLineResult.is_overridden == True, ForecastLineResult.override_value),  # noqa: E712
        else_=ForecastLineResult.p50,
    )

    fc_totals: dict[int, float] = defaultdict(float)
    if li_ids:
        for lid, total in (
            db.query(ForecastLineResult.line_item_id, func.coalesce(func.sum(effective_p50), 0.0))
            .filter(
                ForecastLineResult.version_id == version.id,
                ForecastLineResult.line_item_id.in_(li_ids),
            )
            .group_by(ForecastLineResult.line_item_id)
            .all()
        ):
            fc_totals[int(lid)] = float(total or 0)

    budget_totals: dict[int, float] = defaultdict(float)
    if budget and li_ids:
        for lid, total in (
            db.query(BudgetLineItem.line_item_id, func.coalesce(func.sum(BudgetLineItem.value), 0.0))
            .filter(
                BudgetLineItem.budget_version_id == budget.id,
                BudgetLineItem.line_item_id.in_(li_ids),
            )
            .group_by(BudgetLineItem.line_item_id)
            .all()
        ):
            budget_totals[int(lid)] = float(total or 0)

    prior_totals: dict[int, float] = defaultdict(float)
    if prior and li_ids:
        for lid, total in (
            db.query(ForecastLineResult.line_item_id, func.coalesce(func.sum(effective_p50), 0.0))
            .filter(
                ForecastLineResult.version_id == prior.id,
                ForecastLineResult.line_item_id.in_(li_ids),
            )
            .group_by(ForecastLineResult.line_item_id)
            .all()
        ):
            prior_totals[int(lid)] = float(total or 0)

    ytd_totals: dict[int, float] = defaultdict(float)
    if li_ids:
        for lid, total in (
            db.query(ActualsRecord.line_item_id, func.coalesce(func.sum(ActualsRecord.value), 0.0))
            .filter(ActualsRecord.line_item_id.in_(li_ids))
            .group_by(ActualsRecord.line_item_id)
            .all()
        ):
            ytd_totals[int(lid)] = float(total or 0)

    from app.services.variance_attribution import BridgeAttributionContext, attribute_bridge_row

    attr_ctx = BridgeAttributionContext(db, version.id) if attribute else None
    page_start = (page - 1) * page_size
    page_end = page_start + page_size

    rows_out = []
    for idx, li in enumerate(line_items):
        fc_total = fc_totals.get(li.id, 0.0)
        budget_total = budget_totals.get(li.id, 0.0)
        prior_total = prior_totals.get(li.id, 0.0)
        ytd_actuals = ytd_totals.get(li.id, 0.0)

        var_budget = fc_total - budget_total
        var_prior = fc_total - prior_total
        var_budget_pct = (var_budget / abs(budget_total) * 100) if budget_total else None
        material = abs(var_budget_pct or 0) >= materiality_pct if budget_total else False

        row = {
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
        }
        if attribute and (material or (page_start <= idx < page_end)):
            row["attribution"] = attribute_bridge_row(
                db,
                line_item_id=li.id,
                version_id=version.id,
                variance_vs_prior=var_prior if abs(var_prior) >= abs(var_budget) else var_budget,
                convention=convention,
                ctx=attr_ctx,
            )
        rows_out.append(row)

    total = len(rows_out)
    start = (page - 1) * page_size
    page_rows = rows_out[start : start + page_size]

    # Aggregate waterfall for material rows when attribute=true
    waterfall = None
    if attribute:
        material_rows = [r for r in rows_out if r.get("material") or r.get("attribution")]
        buckets_sum: dict[str, float] = defaultdict(float)
        for r in material_rows:
            attr = r.get("attribution") or {}
            for k, v in (attr.get("buckets") or {}).items():
                buckets_sum[k] += float(v or 0)
        if buckets_sum:
            from app.services.variance_attribution import _bridge_waterfall

            start_val = sum(r.get("prior_forecast", 0) for r in material_rows)
            end_val = sum(r.get("current_forecast", 0) for r in material_rows)
            waterfall = _bridge_waterfall(
                title="Variance bridge (attributed)",
                start_label="Prior",
                start_value=start_val,
                buckets=list(buckets_sum.items()),
                end_label="Current",
                end_value=end_val,
            )

    return {
        "version_id": version.id,
        "budget_version_id": budget.id if budget else None,
        "prior_version_id": prior.id if prior else None,
        "materiality_pct": materiality_pct,
        "attribute": attribute,
        "convention": convention if attribute else None,
        "total": total,
        "page": page,
        "page_size": page_size,
        "rows": page_rows,
        "waterfall": waterfall,
    }


@router.get("/driver-drilldown/{version_id}")
async def driver_drilldown(
    version_id: str,
    line_item_id: int | None = None,
    period_from: str | None = None,
    period_to: str | None = None,
    basis: str = Query("auto"),
    convention: str = Query("volume_first"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Honest variance attribution (Phase 6a) — no keyword bucketing.

    Returns ``attribute_variance`` results per line. Override free-text is
    surfaced under ``unattributed`` with ``explained_pct: 0``. Keyword
    volume/price/mix heuristics have been retired.
    """
    from app.models.driver_input import DriverInput
    from app.services.variance_attribution import attribute_variance

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
    override_rows = overrides.all()

    drivers = db.query(DriverInput).filter(DriverInput.version_id == version_id).all()
    if not getattr(current_user.role, "can_view_all_bus", False) and not (
        current_user.role and current_user.role.can_admin
    ):
        bu = current_user.business_unit
        drivers = [d for d in drivers if not d.business_unit or d.business_unit == bu]

    target_ids = sorted({o.line_item_id for o in override_rows})
    if line_item_id and line_item_id not in target_ids:
        target_ids = [line_item_id]
    if not target_ids and line_item_id:
        target_ids = [line_item_id]

    li_map = {
        li.id: li
        for li in db.query(LineItem).filter(LineItem.id.in_(target_ids)).all()
    } if target_ids else {}

    attributions = []
    for lid in target_ids:
        li = li_map.get(lid)
        if li is not None and not user_can_view_line_item(current_user, li):
            continue
        attr = attribute_variance(
            db,
            line_item_id=lid,
            period_from=period_from,
            period_to=period_to,
            basis=basis,
            convention=convention,
            version_id=version_id,
        )
        attributions.append({
            "line_item_id": lid,
            "line_item": li.name if li else str(lid),
            **attr,
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
        "attributions": attributions,
        # Backward-compatible empty shape — keyword buckets retired
        "override_breakdown": [],
        "submitted_driver_fields": driver_fields,
        "note": (
            "Keyword volume/price/mix bucketing has been retired. "
            "See attributions[].method (fx_only | override_reason_text)."
        ),
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
