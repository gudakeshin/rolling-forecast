"""Side panel data endpoints -- serve detailed views triggered from chat."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.api.auth import get_current_user
from app.models.user import User
from app.models.forecast import ForecastVersion, ForecastLineResult, ModelMetadata
from app.models.line_item import LineItem
from app.models.override import Override
from app.schemas.forecast import PanelDataResponse
from app.services.permissions import require_permission

router = APIRouter(prefix="/panel", tags=["panel"])


def _extract_exog_payload(parameters: dict | None) -> dict | None:
    """Return a compact exog payload for panel UI, if present."""
    if not isinstance(parameters, dict):
        return None
    exog_spec = parameters.get("exog_spec")
    if not isinstance(exog_spec, dict):
        return None
    return {
        "mode": exog_spec.get("mode"),
        "columns": exog_spec.get("columns") or [],
        "drivers": exog_spec.get("drivers") or [],
        "exog_mode_scores": exog_spec.get("exog_mode_scores"),
    }


def _version_payload(v: ForecastVersion) -> dict:
    return {
        "id": v.id,
        "name": v.name,
        "label": v.label,
        "status": v.status,
        "version_type": v.version_type,
        "scenario": getattr(v, "scenario", None) or "base",
        "horizon_months": v.horizon_months,
        "base_period": v.base_period,
        "total_line_items": v.total_line_items,
        "high_confidence_count": v.high_confidence_count or 0,
        "medium_confidence_count": v.medium_confidence_count or 0,
        "low_confidence_count": v.low_confidence_count or 0,
        "override_count": v.override_count or 0,
        "generation_time_seconds": v.generation_time_seconds,
        "created_at": v.created_at.isoformat() if v.created_at else None,
    }


@router.get("/versions")
async def list_versions(
    limit: int = Query(50, ge=1, le=200),
    scenario: str | None = Query(None, description="Filter by scenario label (e.g. base, upside)"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List recent forecast versions (newest first) for the version picker."""
    q = db.query(ForecastVersion)
    if scenario:
        q = q.filter(ForecastVersion.scenario == scenario)
    versions = q.order_by(ForecastVersion.created_at.desc()).limit(limit).all()
    return [_version_payload(v) for v in versions]


@router.get("/version/{version_id}")
async def get_version_detail(
    version_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get a single forecast version by id."""
    v = db.query(ForecastVersion).filter(ForecastVersion.id == version_id).first()
    if not v:
        raise HTTPException(status_code=404, detail="Forecast version not found")
    return _version_payload(v)


@router.get("/forecast-table/{version_id}", response_model=PanelDataResponse)
async def get_forecast_table(
    version_id: str,
    period: str | None = Query(None, description="Filter by period"),
    category: str | None = Query(None, description="Filter by category"),
    confidence_level: str | None = Query(None, description="Filter by confidence level"),
    view: str | None = Query("summary", description="'summary' groups by line item, 'detail' shows all periods"),
    limit: int | None = Query(None, ge=1, le=500, description="Page size (defaults to panel_page_size)"),
    offset: int = Query(0, ge=0, description="Row offset for pagination"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get forecast table data with AI analysis, confidence scores, and remediation.

    When view=summary (default): returns one row per line item with aggregated stats.
    When view=detail: returns all periods (original behavior).
    Summary aggregates and quality_summary are computed over the full filtered set;
    ``rows`` are paginated via limit/offset.
    """
    page_size = limit or settings.panel_page_size
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

    # Full filtered set for aggregates + grouping; only a page of rows is returned
    all_filtered = query.order_by(LineItem.display_order, ForecastLineResult.period).all()
    metadata_by_result_id: dict[str, dict] = {}
    result_ids = [str(r.id) for r in all_filtered]
    if result_ids:
        metadata_rows = (
            db.query(ModelMetadata.line_result_id, ModelMetadata.parameters)
            .filter(ModelMetadata.line_result_id.in_(result_ids))
            .all()
        )
        metadata_by_result_id = {
            str(line_result_id): (parameters if isinstance(parameters, dict) else {})
            for line_result_id, parameters in metadata_rows
        }

    all_scores = [r.confidence_score for r in all_filtered]
    avg_score = sum(all_scores) / len(all_scores) if all_scores else 0
    critical_count = sum(1 for r in all_filtered if r.ai_recommendation in ("override", "manual_input"))
    warning_count = sum(1 for r in all_filtered if r.ai_recommendation == "review")
    ok_count = sum(1 for r in all_filtered if r.ai_recommendation == "approve")
    available_categories = sorted(
        {r.line_item.category for r in all_filtered if r.line_item and r.line_item.category}
    )

    if view == "detail":
        rows = []
        for r in all_filtered:
            exog_payload = _extract_exog_payload(metadata_by_result_id.get(str(r.id)))
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
                "ai_recommendation": r.ai_recommendation,
                "ai_reasoning": r.ai_reasoning,
                "ai_risk_score": r.ai_risk_score,
                "review_status": r.review_status,
                "exog_used": bool(exog_payload),
                "exog": exog_payload,
            })
    else:
        from collections import defaultdict
        groups: dict[int, list[ForecastLineResult]] = defaultdict(list)
        for r in all_filtered:
            groups[r.line_item_id].append(r)

        rows = []
        for li_id, group in groups.items():
            li = group[0].line_item
            worst = min(group, key=lambda x: x.confidence_score)
            exog_payload = _extract_exog_payload(metadata_by_result_id.get(str(worst.id)))
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
                "ai_recommendation": worst.ai_recommendation,
                "ai_reasoning": worst.ai_reasoning,
                "ai_risk_score": worst.ai_risk_score,
                "review_status": worst.review_status,
                "exog_used": bool(exog_payload),
                "exog": exog_payload,
            })

        rows.sort(key=lambda x: (x.get("indent_level", 0), x.get("line_item_name", "")))

    total_count = len(rows)
    page_rows = rows[offset : offset + page_size]
    has_more = offset + page_size < total_count

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
            "rows": page_rows,
            "total_count": total_count,
            "limit": page_size,
            "offset": offset,
            "has_more": has_more,
            "available_categories": available_categories,
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

    li_ids = {o.line_item_id for o in overrides}
    line_items_by_id = {
        li.id: li
        for li in (
            db.query(LineItem).filter(LineItem.id.in_(li_ids)).all() if li_ids else []
        )
    }

    items = []
    for o in overrides:
        li = line_items_by_id.get(o.line_item_id)
        change_pct = ((o.override_value - o.original_model_value) / abs(o.original_model_value) * 100) if o.original_model_value != 0 else 0
        items.append({
            "id": o.id,
            "line_item_id": o.line_item_id,
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


@router.post("/overrides/{override_id}/revert")
async def revert_override_endpoint(
    override_id: str,
    current_user: User = Depends(require_permission("override")),
    db: Session = Depends(get_db),
):
    """Revert an active override and restore the original model value."""
    from app.services.overrides import revert_override

    return revert_override(db, override_id, current_user)


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

        ref = ra or rb
        if ref is None:
            continue
        rows.append({
            "line_item_id": lid,
            "line_item_name": ref.line_name,
            "category": ref.category,
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
            "version_a": {
                "id": va.id,
                "name": va.name,
                "scenario": getattr(va, "scenario", None) or "base",
            },
            "version_b": {
                "id": vb.id,
                "name": vb.name,
                "scenario": getattr(vb, "scenario", None) or "base",
            },
            "rows": rows,
            "total_count": len(rows),
        },
    )
