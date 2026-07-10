"""Side panel data endpoints -- serve detailed views triggered from chat."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.api.auth import get_current_user
from app.models.user import User
from app.models.forecast import ForecastVersion, ForecastLineResult
from app.models.line_item import LineItem
from app.models.override import Override
from app.schemas.forecast import PanelDataResponse

router = APIRouter(prefix="/panel", tags=["panel"])


@router.get("/forecast-table/{version_id}", response_model=PanelDataResponse)
async def get_forecast_table(
    version_id: str,
    period: str | None = Query(None, description="Filter by period"),
    category: str | None = Query(None, description="Filter by category"),
    confidence_level: str | None = Query(None, description="Filter by confidence level"),
    view: str | None = Query("summary", description="'summary' groups by line item, 'detail' shows all periods"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get forecast table data with AI analysis, confidence scores, and remediation.

    When view=summary (default): returns one row per line item with aggregated stats.
    When view=detail: returns all periods (original behavior).
    """
    version = db.query(ForecastVersion).filter(ForecastVersion.id == version_id).first()
    if not version:
        raise HTTPException(status_code=404, detail="Forecast version not found")

    query = (
        db.query(ForecastLineResult)
        .join(LineItem)
        .filter(ForecastLineResult.version_id == version_id)
    )

    # BU-level read authorization
    from app.services.permissions import line_item_scope_filter
    query = line_item_scope_filter(query, current_user, LineItem)

    if period:
        query = query.filter(ForecastLineResult.period == period)
    if category:
        query = query.filter(LineItem.category == category)
    if confidence_level:
        query = query.filter(ForecastLineResult.confidence_level == confidence_level)

    results = query.order_by(LineItem.display_order, ForecastLineResult.period).all()

    if view == "detail":
        # Original detailed view — one row per period
        rows = []
        for r in results:
            rows.append({
                "id": r.id,
                "line_item_id": r.line_item_id,
                "line_item_name": r.line_item.name,
                "account_code": r.line_item.account_code,
                "category": r.line_item.category,
                "period": r.period,
                "p10": r.p10,
                "p50": r.p50,
                "p90": r.p90,
                "confidence_score": r.confidence_score,
                "confidence_level": r.confidence_level,
                "model_type": r.model_type,
                "model_mape": r.model_mape,
                "is_overridden": r.is_overridden,
                "override_value": r.override_value,
                "indent_level": r.line_item.indent_level,
                "is_subtotal": r.line_item.is_subtotal,
                # AI analysis & remediation
                "ai_recommendation": r.ai_recommendation,
                "ai_reasoning": r.ai_reasoning,
                "ai_risk_score": r.ai_risk_score,
                "review_status": r.review_status,
            })
    else:
        # Summary view — one row per line item with aggregated stats
        from collections import defaultdict
        groups: dict[int, list[ForecastLineResult]] = defaultdict(list)
        for r in results:
            groups[r.line_item_id].append(r)

        rows = []
        for li_id, group in groups.items():
            li = group[0].line_item
            worst = min(group, key=lambda x: x.confidence_score)
            avg_conf = sum(r.confidence_score for r in group) / len(group) if group else 0
            total_p50 = sum(r.p50 for r in group)
            overridden_count = sum(1 for r in group if r.is_overridden)

            rows.append({
                "id": worst.id,
                "line_item_id": li_id,
                "line_item_name": li.name,
                "account_code": li.account_code,
                "category": li.category,
                "period_count": len(group),
                "periods": f"{group[0].period} – {group[-1].period}",
                "total_p50": round(total_p50, 2),
                "avg_p50": round(total_p50 / len(group), 2) if group else 0,
                "p10_range": round(min(r.p10 for r in group if r.p10 is not None), 2) if any(r.p10 is not None for r in group) else None,
                "p90_range": round(max(r.p90 for r in group if r.p90 is not None), 2) if any(r.p90 is not None for r in group) else None,
                "min_confidence": round(worst.confidence_score, 1),
                "avg_confidence": round(avg_conf, 1),
                "confidence_level": worst.confidence_level,
                "model_type": worst.model_type,
                "model_mape": worst.model_mape,
                "is_overridden": overridden_count > 0,
                "override_count": overridden_count,
                "indent_level": li.indent_level,
                "is_subtotal": li.is_subtotal,
                # AI analysis & remediation
                "ai_recommendation": worst.ai_recommendation,
                "ai_reasoning": worst.ai_reasoning,
                "ai_risk_score": worst.ai_risk_score,
                "review_status": worst.review_status,
            })

        # Sort by display_order (preserve P&L structure)
        rows.sort(key=lambda x: (x.get("indent_level", 0), x.get("line_item_name", "")))

    # Compute quality summary stats
    all_scores = [r.confidence_score for r in results]
    avg_score = sum(all_scores) / len(all_scores) if all_scores else 0

    critical_count = sum(1 for r in results if r.ai_recommendation in ("override", "manual_input"))
    warning_count = sum(1 for r in results if r.ai_recommendation == "review")
    ok_count = sum(1 for r in results if r.ai_recommendation == "approve")

    return PanelDataResponse(
        panel_type="forecast_table",
        title=f"Forecast: {version.name}",
        data={
            "version": {
                "id": version.id,
                "name": version.name,
                "status": version.status,
                "total_line_items": version.total_line_items,
                "high_confidence_count": version.high_confidence_count or 0,
                "medium_confidence_count": version.medium_confidence_count or 0,
                "low_confidence_count": version.low_confidence_count or 0,
            },
            "quality_summary": {
                "avg_confidence": round(avg_score, 1),
                "critical_count": critical_count,
                "warning_count": warning_count,
                "ok_count": ok_count,
                "total_scored": len(all_scores),
            },
            "view": view,
            "rows": rows,
            "total_count": len(rows),
            "available_categories": sorted(set(r.line_item.category for r in results if r.line_item)),
        },
    )


@router.get("/review-queue/{version_id}", response_model=PanelDataResponse)
async def get_review_queue(
    version_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get items that need review, sorted by materiality."""
    from app.services.permissions import line_item_scope_filter

    results = (
        line_item_scope_filter(
            db.query(ForecastLineResult)
            .join(LineItem)
            .filter(
                ForecastLineResult.version_id == version_id,
                ForecastLineResult.confidence_level.in_(["low", "medium"]),
            ),
            current_user,
            LineItem,
        )
        .order_by(ForecastLineResult.confidence_score.asc())
        .all()
    )

    items = []
    for r in results:
        items.append({
            "id": r.id,
            "line_item_name": r.line_item.name,
            "category": r.line_item.category,
            "period": r.period,
            "p50": r.p50,
            "confidence_score": r.confidence_score,
            "confidence_level": r.confidence_level,
            "model_type": r.model_type,
            "is_overridden": r.is_overridden,
        })

    return PanelDataResponse(
        panel_type="review_queue",
        title="Review Queue",
        data={"items": items, "total_count": len(items)},
    )


@router.get("/overrides/{version_id}", response_model=PanelDataResponse)
async def get_overrides_panel(
    version_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get all overrides for a version to display in the side panel."""
    version = db.query(ForecastVersion).filter(ForecastVersion.id == version_id).first()
    if not version:
        raise HTTPException(status_code=404, detail="Forecast version not found")

    overrides = (
        db.query(Override)
        .filter(Override.version_id == version_id)
        .order_by(Override.created_at.desc())
        .all()
    )

    items = []
    for o in overrides:
        li = db.query(LineItem).filter(LineItem.id == o.line_item_id).first()
        change_pct = ((o.override_value - o.original_model_value) / abs(o.original_model_value) * 100) if o.original_model_value != 0 else 0
        items.append({
            "id": o.id,
            "line_item_name": li.name if li else "Unknown",
            "period": o.period,
            "original_value": o.original_model_value,
            "override_value": o.override_value,
            "change_pct": round(change_pct, 1),
            "reason": o.reason,
            "status": o.status,
            "carry_forward": o.carry_forward,
            "downstream_recalc": o.downstream_recalc_count,
            "created_at": o.created_at.isoformat() if o.created_at else None,
        })

    return PanelDataResponse(
        panel_type="overrides",
        title=f"Overrides: {version.name}",
        data={
            "version": {
                "id": version.id,
                "name": version.name,
                "override_count": version.override_count,
            },
            "items": items,
            "total_count": len(items),
        },
    )


@router.get("/comparison/{version_id_a}/{version_id_b}", response_model=PanelDataResponse)
async def get_comparison_panel(
    version_id_a: str,
    version_id_b: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get version comparison data for the side panel."""
    from sqlalchemy import func

    va = db.query(ForecastVersion).filter(ForecastVersion.id == version_id_a).first()
    vb = db.query(ForecastVersion).filter(ForecastVersion.id == version_id_b).first()
    if not va or not vb:
        raise HTTPException(status_code=404, detail="One or both versions not found")

    # Get aggregated results for both
    from app.services.permissions import line_item_scope_filter

    def get_results(vid):
        return (
            line_item_scope_filter(
                db.query(
                    ForecastLineResult.line_item_id,
                    LineItem.name.label("line_name"),
                    LineItem.category,
                    func.sum(ForecastLineResult.p50).label("total"),
                )
                .join(LineItem)
                .filter(ForecastLineResult.version_id == vid),
                current_user,
                LineItem,
            )
            .group_by(ForecastLineResult.line_item_id, LineItem.name, LineItem.category)
            .all()
        )

    results_a = {r.line_item_id: r for r in get_results(version_id_a)}
    results_b = {r.line_item_id: r for r in get_results(version_id_b)}

    all_ids = set(results_a.keys()) | set(results_b.keys())
    rows = []
    for lid in all_ids:
        ra = results_a.get(lid)
        rb = results_b.get(lid)
        val_a = ra.total if ra else 0
        val_b = rb.total if rb else 0
        variance = val_a - val_b
        pct = (variance / abs(val_b) * 100) if val_b != 0 else 0

        rows.append({
            "line_item_id": lid,
            "line_item_name": (ra or rb).line_name,
            "category": (ra or rb).category,
            "value_a": round(val_a, 2),
            "value_b": round(val_b, 2),
            "variance": round(variance, 2),
            "pct_change": round(pct, 1),
        })

    rows.sort(key=lambda x: abs(x["pct_change"]), reverse=True)

    return PanelDataResponse(
        panel_type="comparison",
        title=f"Comparison: {va.name} vs {vb.name}",
        data={
            "version_a": {"id": va.id, "name": va.name},
            "version_b": {"id": vb.id, "name": vb.name},
            "rows": rows,
            "total_count": len(rows),
        },
    )
