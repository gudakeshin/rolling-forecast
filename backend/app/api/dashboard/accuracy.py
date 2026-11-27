"""Value metrics and accuracy tracking (vintages, FVA, process diagnostics)."""


import logging

from fastapi import Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.auth import get_current_user
from app.database import get_db
from app.models.actuals import ActualsRecord
from app.models.forecast import ForecastLineResult, ForecastVersion
from app.models.line_item import LineItem
from app.models.user import User
from app.schemas.forecast import PanelDataResponse
from app.services.permissions import (
    get_accessible_forecast_version,
    line_item_scope_filter,
)

# Scoring lives in services.confidence; remediation stays on the skill module.

logger = logging.getLogger(__name__)


from app.api.dashboard._common import router

# ──────────────────────────────────────────────────
# Accuracy Tracking
# ──────────────────────────────────────────────────

@router.get("/value-metrics")
async def get_value_metrics(
    business_unit_id: str | None = None,
    limit: int = Query(12, ge=2, le=50),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Cycle time, override rate, review focus and FVA vs naive across the
    company's recent versions, with first-to-latest trends (plan 6.4)."""
    from app.services.permissions import can_access_business_unit
    from app.services.value_metrics import compute_value_metrics

    bu_id = business_unit_id or current_user.business_unit_id
    if not bu_id or not can_access_business_unit(current_user, bu_id):
        raise HTTPException(404, "Business unit not found")
    return compute_value_metrics(db, business_unit_id=bu_id, limit=limit)


@router.get("/accuracy-tracking/{version_id}", response_model=PanelDataResponse)
async def get_accuracy_tracking(
    version_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Accuracy tracking from vintage forecast_accuracy_records (fallback: live join)."""
    from app.models.fx import ForecastAccuracyRecord

    version = get_accessible_forecast_version(db, current_user, version_id)

    vintage_q = (
        db.query(ForecastAccuracyRecord, LineItem)
        .join(LineItem, LineItem.id == ForecastAccuracyRecord.line_item_id)
        .filter(ForecastAccuracyRecord.version_id == version_id)
    )
    vintage_q = line_item_scope_filter(vintage_q, current_user, LineItem)
    vintage_rows = vintage_q.all()

    accuracy_items = []
    model_mapes: dict[str, list[float]] = {}
    category_mapes: dict[str, list[float]] = {}
    horizon_buckets: dict[str, list[float]] = {"1m": [], "2-3m": [], "4-6m": [], "7m+": []}

    if vintage_rows:
        for rec, li in vintage_rows:
            mape = float(rec.pct_error) if rec.pct_error is not None else None
            bias = None
            if rec.actual != 0:
                bias = (rec.predicted_p50 - rec.actual) / abs(rec.actual) * 100
            item = {
                "line_item_id": li.id,
                "line_item_name": li.name,
                "category": li.category,
                "period": rec.period,
                "horizon_offset": rec.horizon_offset,
                "forecast": round(rec.predicted_p50, 2),
                "published": round(rec.predicted_p50, 2),
                "model_forecast": round(rec.model_p50, 2) if rec.model_p50 is not None else None,
                "naive": round(rec.naive_p50, 2) if rec.naive_p50 is not None else None,
                "seasonal_naive": (
                    round(rec.seasonal_naive_p50, 2)
                    if rec.seasonal_naive_p50 is not None
                    else None
                ),
                "actual": round(rec.actual, 2),
                "mape": round(mape, 2) if mape is not None else None,
                "bias": round(bias, 2) if bias is not None else None,
                "hit_range": rec.within_p10_p90,
                "model_type": rec.model_type,
                "confidence_score": None,
                "source": "vintage",
            }
            accuracy_items.append(item)
            if mape is not None:
                model = rec.model_type or "unknown"
                model_mapes.setdefault(model, []).append(mape)
                category_mapes.setdefault(li.category, []).append(mape)
                h = rec.horizon_offset
                if h <= 1:
                    horizon_buckets["1m"].append(mape)
                elif h <= 3:
                    horizon_buckets["2-3m"].append(mape)
                elif h <= 6:
                    horizon_buckets["4-6m"].append(mape)
                else:
                    horizon_buckets["7m+"].append(mape)
    else:
        # Fallback: live join when no vintage snapshots yet (one query, not per-row)
        live_q = (
            db.query(ForecastLineResult, ActualsRecord, LineItem)
            .select_from(ForecastLineResult)
            .join(LineItem, LineItem.id == ForecastLineResult.line_item_id)
            .join(
                ActualsRecord,
                (ActualsRecord.line_item_id == ForecastLineResult.line_item_id)
                & (ActualsRecord.period == ForecastLineResult.period),
            )
            .filter(ForecastLineResult.version_id == version_id)
        )
        live_q = line_item_scope_filter(live_q, current_user, LineItem)
        for r, actual, li in live_q.all():
            if actual.value == 0:
                continue
            mape = abs(r.p50 - actual.value) / abs(actual.value) * 100
            bias = (r.p50 - actual.value) / abs(actual.value) * 100
            hit_range = (
                r.p10 is not None and r.p90 is not None
                and r.p10 <= actual.value <= r.p90
            )
            item = {
                "line_item_id": r.line_item_id,
                "line_item_name": li.name,
                "category": li.category,
                "period": r.period,
                "horizon_offset": None,
                "forecast": round(r.p50, 2),
                "actual": round(actual.value, 2),
                "mape": round(mape, 2),
                "bias": round(bias, 2),
                "hit_range": hit_range,
                "model_type": r.model_type,
                "confidence_score": r.confidence_score,
                "source": "live",
            }
            accuracy_items.append(item)
            model = r.model_type or "unknown"
            model_mapes.setdefault(model, []).append(mape)
            category_mapes.setdefault(li.category, []).append(mape)

    model_performance = []
    for model, mapes in sorted(model_mapes.items()):
        avg_mape = sum(mapes) / len(mapes) if mapes else 0
        model_performance.append({
            "model": model,
            "avg_mape": round(avg_mape, 2),
            "count": len(mapes),
            "best_mape": round(min(mapes), 2) if mapes else 0,
            "worst_mape": round(max(mapes), 2) if mapes else 0,
        })

    category_accuracy_fixed = []
    for cat, mapes in sorted(category_mapes.items()):
        avg_mape = sum(mapes) / len(mapes) if mapes else 0
        cat_items = [i for i in accuracy_items if i["category"] == cat]
        cat_forecast_sum = sum(i["forecast"] for i in cat_items)
        cat_actual_sum = sum(i["actual"] for i in cat_items)
        cat_bias = (
            (cat_forecast_sum - cat_actual_sum) / abs(cat_actual_sum) * 100
            if abs(cat_actual_sum) > 1e-10 else 0
        )
        category_accuracy_fixed.append({
            "category": cat,
            "avg_mape": round(avg_mape, 2),
            "avg_bias": round(cat_bias, 2),
            "count": len(mapes),
        })

    by_horizon = []
    for label, mapes in horizon_buckets.items():
        if not mapes:
            continue
        by_horizon.append({
            "horizon": label,
            "avg_mape": round(sum(mapes) / len(mapes), 2),
            "count": len(mapes),
        })

    # MAPE trend across versions using vintage records when present
    recent_versions = (
        db.query(ForecastVersion)
        .order_by(ForecastVersion.created_at.desc())
        .limit(6)
        .all()
    )
    recent_version_ids = [v.id for v in recent_versions]
    accuracy_by_version: dict[str, list] = {vid: [] for vid in recent_version_ids}
    if recent_version_ids:
        for rec in (
            db.query(ForecastAccuracyRecord)
            .filter(ForecastAccuracyRecord.version_id.in_(recent_version_ids))
            .all()
        ):
            accuracy_by_version.setdefault(rec.version_id, []).append(rec)

    # Fallback join for versions with no vintage rows — one query for all such versions
    versions_needing_fallback = [
        v.id for v in recent_versions if not accuracy_by_version.get(v.id)
    ]
    fallback_by_version: dict[str, list[tuple[float, float]]] = {
        vid: [] for vid in versions_needing_fallback
    }
    if versions_needing_fallback:
        for vr, act in (
            db.query(ForecastLineResult, ActualsRecord)
            .join(
                ActualsRecord,
                (ActualsRecord.line_item_id == ForecastLineResult.line_item_id)
                & (ActualsRecord.period == ForecastLineResult.period),
            )
            .filter(ForecastLineResult.version_id.in_(versions_needing_fallback))
            .all()
        ):
            if act.value != 0:
                fallback_by_version.setdefault(vr.version_id, []).append(
                    (float(vr.p50), float(act.value))
                )

    mape_trend = []
    bias_trend = []
    for v in reversed(recent_versions):
        v_recs = accuracy_by_version.get(v.id) or []
        if v_recs:
            mapes_for_version = [r.pct_error for r in v_recs if r.pct_error is not None]
            v_sum_forecast = sum(r.predicted_p50 for r in v_recs)
            v_sum_actual = sum(r.actual for r in v_recs)
        else:
            mapes_for_version = []
            v_sum_forecast = 0.0
            v_sum_actual = 0.0
            for pred, act_val in fallback_by_version.get(v.id, []):
                mapes_for_version.append(abs(pred - act_val) / abs(act_val) * 100)
                v_sum_forecast += pred
                v_sum_actual += act_val

        avg_mape = sum(mapes_for_version) / len(mapes_for_version) if mapes_for_version else 0
        mape_trend.append({
            "version": v.name[:15],
            "avg_mape": round(avg_mape, 2),
            "data_points": len(mapes_for_version),
        })
        v_agg_bias = (
            (v_sum_forecast - v_sum_actual) / abs(v_sum_actual) * 100
            if abs(v_sum_actual) > 1e-10 else 0
        )
        bias_trend.append({
            "version": v.name[:15],
            "avg_bias": round(v_agg_bias, 2),
            "data_points": len(mapes_for_version),
        })

    all_mapes = [i["mape"] for i in accuracy_items if i.get("mape") is not None]
    hit_count = sum(1 for i in accuracy_items if i.get("hit_range"))
    total_forecast_sum = sum(i["forecast"] for i in accuracy_items)
    total_actual_sum = sum(i["actual"] for i in accuracy_items)
    aggregate_bias_pct = (
        (total_forecast_sum - total_actual_sum) / abs(total_actual_sum) * 100
        if abs(total_actual_sum) > 1e-10 else 0
    )

    # WAPE / FVA from vintage fields when present
    from app.services.accuracy_snapshot import _wape, compute_fva

    pub_vals = [float(i["forecast"]) for i in accuracy_items]
    act_vals = [float(i["actual"]) for i in accuracy_items]
    model_vals = [
        float(i["model_forecast"]) if i.get("model_forecast") is not None else float(i["forecast"])
        for i in accuracy_items
    ]
    naive_pairs = [
        (float(i["naive"]), float(i["actual"]))
        for i in accuracy_items
        if i.get("naive") is not None
    ]
    snaive_pairs = [
        (float(i["seasonal_naive"]), float(i["actual"]))
        for i in accuracy_items
        if i.get("seasonal_naive") is not None
    ]
    published_wape = _wape(pub_vals, act_vals)
    model_wape = _wape(model_vals, act_vals)
    naive_wape = _wape([p for p, _ in naive_pairs], [a for _, a in naive_pairs]) if naive_pairs else None
    seasonal_naive_wape = (
        _wape([p for p, _ in snaive_pairs], [a for _, a in snaive_pairs]) if snaive_pairs else None
    )
    fva_vs_naive = (
        compute_fva(pub_vals, act_vals, [p for p, _ in naive_pairs])
        if naive_pairs and len(naive_pairs) == len(pub_vals)
        else (None if naive_wape is None or published_wape is None else naive_wape - published_wape)
    )
    fva_vs_seasonal = (
        None
        if seasonal_naive_wape is None or published_wape is None
        else seasonal_naive_wape - published_wape
    )

    from app.services.interval_calibration import crps_from_quantiles

    crps_vals: list[float] = []
    crps_actual = 0.0
    if vintage_rows:
        for rec, _li in vintage_rows:
            c = crps_from_quantiles(
                float(rec.actual),
                {0.1: rec.predicted_p10, 0.5: rec.predicted_p50, 0.9: rec.predicted_p90},
            )
            if c is not None:
                crps_vals.append(c)
                crps_actual += abs(float(rec.actual))
    crps_pct = round(sum(crps_vals) / crps_actual * 100, 2) if crps_vals and crps_actual > 1e-12 else None

    overall = {
        "avg_mape": round(sum(all_mapes) / len(all_mapes), 2) if all_mapes else 0,
        "median_mape": round(sorted(all_mapes)[len(all_mapes) // 2], 2) if all_mapes else 0,
        "avg_bias": round(aggregate_bias_pct, 2),
        "bias_dollar": round(total_forecast_sum - total_actual_sum, 2),
        "published_wape": round(published_wape, 2) if published_wape is not None else None,
        "model_wape": round(model_wape, 2) if model_wape is not None else None,
        "naive_wape": round(naive_wape, 2) if naive_wape is not None else None,
        "seasonal_naive_wape": round(seasonal_naive_wape, 2) if seasonal_naive_wape is not None else None,
        "fva_vs_naive": round(fva_vs_naive, 2) if fva_vs_naive is not None else None,
        "fva_vs_seasonal_naive": round(fva_vs_seasonal, 2) if fva_vs_seasonal is not None else None,
        "bias_direction": "over" if aggregate_bias_pct > 1 else "under" if aggregate_bias_pct < -1 else "neutral",
        "hit_rate": round(hit_count / len(accuracy_items) * 100, 1) if accuracy_items else 0,
        # CRPS from the stored P10/P50/P90, scaled like WAPE (sum over sum of
        # |actual|), so the whole band is scored, not just the median.
        "crps_pct": crps_pct,
        "total_comparisons": len(accuracy_items),
        "source": "vintage" if vintage_rows else "live",
    }

    # Process diagnostics across every closed vintage of this version's lines
    # (Phase F): bias / tracking signal, revision stability, FVA by user and
    # reason. Scoped to lines the caller can see.
    from app.services.accuracy_diagnostics import diagnostics as accuracy_diagnostics

    scoped_lines = line_item_scope_filter(
        db.query(LineItem.id, LineItem.name)
        .join(ForecastLineResult, ForecastLineResult.line_item_id == LineItem.id)
        .filter(ForecastLineResult.version_id == version_id)
        .distinct(),
        current_user,
        LineItem,
    ).all()
    names = {lid: name for lid, name in scoped_lines}
    diag = accuracy_diagnostics(db, list(names))
    for row in diag["bias_signals"]:
        row["line_item_name"] = names.get(row["line_item_id"])
    for row in diag["revision_stability"]["lines"]:
        row["line_item_name"] = names.get(row["line_item_id"])
    diag["bias_signals"] = diag["bias_signals"][:20]
    diag["revision_stability"]["lines"] = diag["revision_stability"]["lines"][:20]

    return PanelDataResponse(
        panel_type="accuracy_tracking",
        title=f"Accuracy Tracking: {version.name}",
        data={
            "version": {"id": version.id, "name": version.name},
            "overall": overall,
            "model_performance": model_performance,
            "category_accuracy": category_accuracy_fixed,
            "by_horizon": by_horizon,
            "mape_trend": mape_trend,
            "bias_trend": bias_trend,
            "top_deviations": sorted(
                [i for i in accuracy_items if i.get("mape") is not None],
                key=lambda x: -x["mape"],
            )[:15],
            "items": accuracy_items[:100],
            "diagnostics": diag,
        },
    )


