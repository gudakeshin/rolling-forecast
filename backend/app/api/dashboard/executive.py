"""Executive dashboard and its net-income risk envelope."""


import logging
from typing import Any

import numpy as np
from fastapi import Depends
from sqlalchemy import false, func
from sqlalchemy.orm import Session

from app.api.auth import get_current_user
from app.database import get_db
from app.models.actuals import ActualsRecord
from app.models.forecast import ForecastLineResult, ForecastVersion
from app.models.line_item import LineItem
from app.models.override import Override
from app.models.user import User
from app.schemas.forecast import PanelDataResponse
from app.services.permissions import (
    can_view_all_bus,
    get_accessible_forecast_version,
    line_item_scope_filter,
)

# Scoring lives in services.confidence; remediation stays on the skill module.
from app.services.quantile_aggregation import DEFAULT_PERIOD_RHO, aggregate_quantiles

logger = logging.getLogger(__name__)


from app.api.dashboard._common import _scoped_results_query, formatCurrency, router


def _executive_risk_envelope(
    db: Session, version: ForecastVersion, results: list[ForecastLineResult]
) -> dict[str, Any]:
    """Risk envelope for the executive dashboard without summing quantiles.

    P50s are summed as before. Downside/upside are quantiles of *net income*
    (revenue lines +, cost lines sign-flipped) aggregated with
    ``services.quantile_aggregation``: line-to-line correlation comes from
    ``reconciliation.estimate_leaf_correlation`` (actuals shrunk toward a
    same-category prior, or the prior alone), month-to-month from an AR(1)
    default. Category rows aggregate one category's lines and report the
    downside in net-income terms (a cost overrun for cost categories).
    """
    from app.services.driver_discovery import line_family
    from app.services.reconciliation import (
        _category_block_correlation,
        estimate_leaf_correlation,
    )

    leaves = [r for r in results if r.line_item and not r.line_item.is_subtotal]
    line_ids = sorted({r.line_item_id for r in leaves})
    periods = sorted({r.period for r in leaves})
    li_pos = {lid: i for i, lid in enumerate(line_ids)}
    p_pos = {p: t for t, p in enumerate(periods)}
    lines: dict[int, LineItem] = {}

    grid = {q: np.full((len(line_ids), len(periods)), np.nan) for q in ("p10", "p50", "p90")}
    for r in leaves:
        lines[r.line_item_id] = r.line_item
        i, t = li_pos[r.line_item_id], p_pos[r.period]
        for q in grid:
            v = getattr(r, q)
            if v is not None:
                grid[q][i, t] = float(v)
    P10, P50, P90 = grid["p10"], grid["p50"], grid["p90"]

    def _line_type(li: LineItem) -> str:
        fam = line_family(li)
        if fam == "expense" or (fam is None and (li.sign_convention or "") == "negative"):
            return "cost"
        return "revenue" if fam == "revenue" else "other"

    types = np.array([_line_type(lines[lid]) for lid in line_ids], dtype=object)
    signs = np.where(types == "cost", -1.0, 1.0)

    categories = {lid: lines[lid].category for lid in line_ids}
    R_line = estimate_leaf_correlation(
        db, line_ids, dataset_id=version.actuals_dataset_id, categories=categories
    )
    corr_source = (
        "category_prior"
        if np.allclose(R_line, _category_block_correlation(line_ids, categories))
        else "actuals_shrunk"
    )

    def _agg(rows: np.ndarray, cols: slice = slice(None), net: bool = False):
        idx = np.flatnonzero(rows)
        return aggregate_quantiles(
            P10[idx][:, cols], P50[idx][:, cols], P90[idx][:, cols],
            signs=signs[idx] if net else None,
            line_corr=R_line[np.ix_(idx, idx)],
        )

    all_rows = np.ones(len(line_ids), dtype=bool)
    net = _agg(all_rows, net=True)
    total_p50 = float(np.nansum(P50))

    risk_by_type: dict[str, dict[str, float]] = {}
    for kind in ("revenue", "cost", "other"):
        rows = types == kind
        if rows.any():
            q = _agg(rows)
            risk_by_type[kind] = {
                "p10": round(q.p10, 2), "p50": round(q.p50, 2), "p90": round(q.p90, 2),
            }

    cat_totals: dict[str, dict[str, Any]] = {}
    cat_arr = np.array([categories[lid] for lid in line_ids], dtype=object)
    for cat in dict.fromkeys(cat_arr):
        rows = cat_arr == cat
        q = _agg(rows)
        is_cost = bool((types[rows] == "cost").all())
        cat_totals[cat] = {
            "p10": q.p10, "p50": q.p50, "p90": q.p90,
            "line_type": "cost" if is_cost else str(types[rows][0]),
            "downside_risk": q.upside if is_cost else q.downside,
            "upside_opportunity": q.downside if is_cost else q.upside,
        }

    time_series = []
    for t, period in enumerate(periods):
        q = _agg(all_rows, slice(t, t + 1), net=True)
        p50 = float(np.nansum(P50[:, t]))
        time_series.append({
            "period": period,
            "p10": round(p50 - q.downside, 2),
            "p50": round(p50, 2),
            "p90": round(p50 + q.upside, 2),
        })

    return {
        "total_p50": total_p50,
        "net_income_p50": net.p50,
        "downside_risk": net.downside,
        "upside_opportunity": net.upside,
        "categories": cat_totals,
        "risk_by_type": risk_by_type,
        "time_series": time_series,
        "risk_method": {
            "line_correlation": corr_source,
            "period_correlation": f"ar1_rho_{DEFAULT_PERIOD_RHO}",
            "interval": "p10_p90",
        },
    }


# ──────────────────────────────────────────────────
# Executive Dashboard
# ──────────────────────────────────────────────────

@router.get("/executive-dashboard/{version_id}", response_model=PanelDataResponse)
async def get_executive_dashboard(
    version_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """CFO strategic dashboard — decision-ready, forward-looking, actionable.

    Organized around what a CFO needs to decide, not what the system computed:
    1. Forecast position with risk envelope
    2. Review cycle progress and what's still pending
    3. Priority items requiring CFO attention (ranked by $ impact)
    4. Forward-looking risk / opportunity analysis
    5. Key business drivers and assumptions
    6. Movement bridge with narrative
    """
    from app.models.driver_input import DriverInput
    from app.services.kpis import evaluate_kpis

    version = get_accessible_forecast_version(db, current_user, version_id)

    # ─── 1. Load all results and build lookups ────────
    all_results = (
        _scoped_results_query(db, current_user, version_id)
        .order_by(LineItem.category, LineItem.display_order, ForecastLineResult.period)
        .all()
    )

    if not all_results:
        return PanelDataResponse(
            panel_type="executive_dashboard",
            title=f"CFO Dashboard: {version.name}",
            data={"version": {"id": version.id, "name": version.name, "status": version.status},
                  "empty": True},
        )

    # Build actuals lookup
    actuals_map: dict[tuple[int, str], float] = {}
    if version.actuals_dataset_id:
        for a in db.query(ActualsRecord).filter(
            ActualsRecord.dataset_id == version.actuals_dataset_id
        ).all():
            actuals_map[(a.line_item_id, a.period)] = a.value

    # Group by line item
    li_groups: dict[int, list[ForecastLineResult]] = {}
    for r in all_results:
        li_groups.setdefault(r.line_item_id, []).append(r)

    # ─── 2. Forecast Position ─────────────────────────
    # P50s add; P10/P90 do not (see _executive_risk_envelope).
    envelope = _executive_risk_envelope(db, version, all_results)
    total_p50 = envelope["total_p50"]
    downside_risk = envelope["downside_risk"]
    upside_opportunity = envelope["upside_opportunity"]
    total_p10 = total_p50 - downside_risk
    total_p90 = total_p50 + upside_opportunity
    cat_totals: dict[str, dict[str, Any]] = envelope["categories"]

    # Delta vs prior version
    prior_total = None
    delta_vs_prior = None
    delta_pct = None
    if version.parent_version_id:
        prior_agg = (
            db.query(func.sum(ForecastLineResult.p50))
            .join(LineItem)
            .filter(
                ForecastLineResult.version_id == version.parent_version_id,
                LineItem.is_subtotal == False,
            )
            .scalar()
        )
        if prior_agg:
            prior_total = float(prior_agg)
            delta_vs_prior = total_p50 - prior_total
            delta_pct = (delta_vs_prior / abs(prior_total) * 100) if abs(prior_total) > 1e-10 else 0

    # ─── 3. Review Progress ───────────────────────────
    unique_li_ids = set(li_groups.keys())
    reviewed_li_ids: set[int] = set()
    approved_li_ids: set[int] = set()
    flagged_li_ids: set[int] = set()
    overridden_li_ids: set[int] = set()

    for r in all_results:
        if r.review_status in ("approved", "rejected"):
            reviewed_li_ids.add(r.line_item_id)
            if r.review_status == "approved":
                approved_li_ids.add(r.line_item_id)
        if r.ai_recommendation == "flag":
            flagged_li_ids.add(r.line_item_id)
        if r.is_overridden:
            overridden_li_ids.add(r.line_item_id)

    total_unique = len(unique_li_ids)
    review_progress = {
        "total_items": total_unique,
        "reviewed": len(reviewed_li_ids),
        "approved": len(approved_li_ids),
        "pending": total_unique - len(reviewed_li_ids),
        "flagged": len(flagged_li_ids - reviewed_li_ids),
        "overridden": len(overridden_li_ids),
        "pct_complete": round(len(reviewed_li_ids) / total_unique * 100, 0) if total_unique else 0,
    }

    # ─── 4. Priority Action Items ─────────────────────
    # Items the CFO needs to focus on: high-materiality, un-reviewed, flagged, or risky
    priority_items: list[dict] = []

    for li_id, group in li_groups.items():
        li = group[0].line_item
        if not li or li.is_subtotal:
            continue

        li_total = sum(abs(r.p50) for r in group)
        mat_pct = li_total / abs(total_p50) * 100 if abs(total_p50) > 1e-10 else 0
        if mat_pct < 1:
            continue  # Skip immaterial items for CFO view

        # Check review status
        is_reviewed = li_id in reviewed_li_ids
        is_flagged = li_id in (flagged_li_ids - reviewed_li_ids)

        # Check AI risk
        worst_risk = max((r.ai_risk_score or 0) for r in group)
        worst_rec = None
        worst_reasoning = None
        for r in group:
            if r.ai_risk_score == worst_risk and r.ai_recommendation:
                worst_rec = r.ai_recommendation
                worst_reasoning = r.ai_reasoning
                break

        # Compute business context
        avg_p50 = li_total / len(group) if group else 0
        li_p10 = sum(float(r.p10 or 0) for r in group) / len(group) if group else 0
        li_p90 = sum(float(r.p90 or 0) for r in group) / len(group) if group else 0
        range_width = li_p90 - li_p10

        # Actuals comparison
        last_actual = None
        actual_delta = None
        for r in reversed(group):
            act_val = actuals_map.get((li_id, r.period))
            if act_val is not None:
                last_actual = act_val
                actual_delta = avg_p50 - act_val
                break

        # Determine priority reason
        priority_reason = ""
        priority_urgency = "low"

        if is_flagged:
            priority_reason = "Flagged for review — AI identified significant concerns"
            priority_urgency = "high"
        elif not is_reviewed and worst_risk and worst_risk > 60:
            priority_reason = f"High risk score ({worst_risk:.0f}/100) — not yet reviewed"
            priority_urgency = "high"
        elif not is_reviewed and mat_pct > 5:
            priority_reason = f"Material line item ({mat_pct:.1f}% of forecast) — pending review"
            priority_urgency = "medium"
        elif not is_reviewed and range_width > abs(avg_p50) * 0.5:
            priority_reason = "Wide forecast range — consider collecting BU assumptions"
            priority_urgency = "medium"
        elif actual_delta and abs(actual_delta) > abs(avg_p50) * 0.2:
            direction = "above" if actual_delta > 0 else "below"
            priority_reason = f"Forecast is {abs(actual_delta / avg_p50 * 100):.0f}% {direction} recent actuals"
            priority_urgency = "medium" if not is_reviewed else "low"
        else:
            continue  # Not a priority item

        if is_reviewed and priority_urgency != "high":
            continue  # Already addressed

        priority_items.append({
            "line_item_name": li.name,
            "line_item_id": li_id,
            "category": li.category,
            "business_unit": li.business_unit,
            "avg_p50": round(avg_p50, 2),
            "total_p50": round(li_total, 2),
            "materiality_pct": round(mat_pct, 1),
            "forecast_range": {"low": round(li_p10, 2), "high": round(li_p90, 2)},
            "risk_score": round(worst_risk, 0) if worst_risk else 0,
            "ai_recommendation": worst_rec,
            "ai_reasoning": worst_reasoning,
            "priority_reason": priority_reason,
            "urgency": priority_urgency,
            "is_reviewed": is_reviewed,
            "is_overridden": li_id in overridden_li_ids,
            "last_actual": round(last_actual, 2) if last_actual else None,
        })

    # Sort: high urgency first, then by materiality
    urgency_order = {"high": 0, "medium": 1, "low": 2}
    priority_items.sort(key=lambda x: (urgency_order.get(x["urgency"], 3), -x["materiality_pct"]))
    priority_items = priority_items[:12]

    # ─── 5. Risk & Opportunity by Category ────────────
    risk_opportunity: list[dict[str, Any]] = []
    for cat, totals in sorted(cat_totals.items(), key=lambda x: -abs(x[1]["p50"])):
        risk_opportunity.append({
            "category": cat,
            "line_type": totals["line_type"],
            "forecast": round(totals["p50"], 2),
            # Net-income terms: for a cost category the downside is an overrun.
            "downside_risk": round(totals["downside_risk"], 2),
            "upside_opportunity": round(totals["upside_opportunity"], 2),
            "range_pct": round((totals["p90"] - totals["p10"]) / abs(totals["p50"]) * 100, 1) if abs(totals["p50"]) > 1e-10 else 0,
        })

    # ─── 6. Key Business Drivers & Assumptions ────────
    # Recent overrides (CFO needs to know what's been manually adjusted) --
    # same BU scope as the forecast results above (_scoped_results_query).
    recent_overrides_q = (
        db.query(Override, LineItem)
        .join(LineItem, Override.line_item_id == LineItem.id)
        .filter(Override.version_id == version_id, Override.status == "active")
    )
    recent_overrides = (
        line_item_scope_filter(recent_overrides_q, current_user, LineItem)
        .order_by(Override.created_at.desc())
        .limit(8)
        .all()
    )
    override_summary = []
    for ov, li in recent_overrides:
        delta = ov.override_value - ov.original_model_value
        override_summary.append({
            "line_item": li.name,
            "category": li.category,
            "period": ov.period,
            "original": round(ov.original_model_value, 2),
            "override": round(ov.override_value, 2),
            "delta": round(delta, 2),
            "delta_pct": round(delta / abs(ov.original_model_value) * 100, 1) if abs(ov.original_model_value) > 1e-10 else 0,
            "reason": ov.reason,
        })

    # Driver input submissions -- same "no shared bucket" BU scope as
    # get_driver_inputs above.
    driver_inputs_q = db.query(DriverInput).filter(DriverInput.version_id == version_id)
    if not can_view_all_bus(current_user):
        if current_user.business_unit_id:
            driver_inputs_q = driver_inputs_q.filter(
                DriverInput.business_unit_id == current_user.business_unit_id
            )
        else:
            driver_inputs_q = driver_inputs_q.filter(false())
    driver_inputs = driver_inputs_q.all()
    driver_summary: dict[str, Any] = {
        "total_submissions": len(driver_inputs),
        "approved": sum(1 for d in driver_inputs if d.status == "approved"),
        "pending": sum(1 for d in driver_inputs if d.status == "submitted"),
        "late": sum(1 for d in driver_inputs if d.is_late),
        "business_units": sorted({d.business_unit for d in driver_inputs if d.business_unit}),
    }

    # ─── 7. Forward-Looking Insights ──────────────────
    insights: list[dict] = []

    # Insight: Forecast concentration risk
    sorted_cats = sorted(cat_totals.items(), key=lambda x: -abs(x[1]["p50"]))
    if sorted_cats:
        top_cat, top_val = sorted_cats[0]
        top_pct = abs(top_val["p50"]) / abs(total_p50) * 100 if abs(total_p50) > 1e-10 else 0
        if top_pct > 40:
            insights.append({
                "type": "risk",
                "title": "Revenue concentration",
                "detail": f"{top_cat} represents {top_pct:.0f}% of the total forecast. A miss in this category would significantly impact overall results.",
                "action": f"Ensure {top_cat} assumptions are validated with BU owners.",
            })

    # Insight: Pending review on material items
    pending_material = [p for p in priority_items if p["urgency"] == "high" and not p["is_reviewed"]]
    if pending_material:
        dollar_pending = sum(p["total_p50"] for p in pending_material)
        insights.append({
            "type": "action",
            "title": f"{len(pending_material)} high-priority items pending review",
            "detail": f"Items representing {formatCurrency(dollar_pending)} in forecast value have been flagged but not yet reviewed.",
            "action": "Open Review Dashboard to address flagged items.",
        })

    # Insight: Wide downside risk
    if abs(total_p50) > 1e-10 and downside_risk / abs(total_p50) * 100 > 15:
        insights.append({
            "type": "risk",
            "title": "Significant downside exposure",
            "detail": f"The P10 scenario is {formatCurrency(downside_risk)} below the base forecast on a net-income basis, a {downside_risk / abs(total_p50) * 100:.0f}% gap. Key drivers: {', '.join(c['category'] for c in risk_opportunity[:3] if c['downside_risk'] > 0)}.",
            "action": "Review line items with widest confidence intervals and consider risk mitigation.",
        })

    # Insight: Over-forecast bias
    if actuals_map:
        sum_f, sum_a = 0.0, 0.0
        for r in all_results:
            act_val = actuals_map.get((r.line_item_id, r.period))
            if act_val and abs(act_val) > 1e-10:
                sum_f += float(r.p50)
                sum_a += float(act_val)
        if abs(sum_a) > 1e-10:
            bias_pct = (sum_f - sum_a) / abs(sum_a) * 100
            if abs(bias_pct) > 5:
                direction = "over" if bias_pct > 0 else "under"
                insights.append({
                    "type": "warning",
                    "title": f"Systematic {direction}-forecasting detected",
                    "detail": f"The forecast is running {abs(bias_pct):.1f}% {direction} actuals ({formatCurrency(abs(sum_f - sum_a))} net). This pattern may persist into future periods.",
                    "action": f"Consider {'reducing' if direction == 'over' else 'increasing'} baseline assumptions or applying a correction factor.",
                })

    # Insight: Override activity
    if override_summary:
        net_override_impact = sum(o["delta"] for o in override_summary)
        insights.append({
            "type": "info",
            "title": f"{len(override_summary)} active manual adjustments",
            "detail": f"Manual overrides have a net impact of {formatCurrency(net_override_impact)} on the forecast. {'This increases' if net_override_impact > 0 else 'This decreases'} the model baseline.",
            "action": "Review override rationale to ensure assumptions are still valid.",
        })

    # Insight: Missing BU inputs
    if driver_summary["pending"] > 0 or driver_summary["late"] > 0:
        insights.append({
            "type": "action",
            "title": "BU driver inputs incomplete",
            "detail": f"{driver_summary['pending']} submissions pending, {driver_summary['late']} overdue. Missing inputs increase forecast uncertainty.",
            "action": "Follow up with pending business units to collect assumptions.",
        })

    # Insight: Upside opportunity
    if abs(total_p50) > 1e-10 and upside_opportunity / abs(total_p50) * 100 > 10:
        top_upside_cats = sorted(risk_opportunity, key=lambda x: -x["upside_opportunity"])[:2]
        insights.append({
            "type": "opportunity",
            "title": f"Upside potential of {formatCurrency(upside_opportunity)}",
            "detail": f"The P90 scenario exceeds base by {upside_opportunity / abs(total_p50) * 100:.0f}%. Largest upside in: {', '.join(c['category'] for c in top_upside_cats)}.",
            "action": "Explore what conditions would unlock the upside scenario.",
        })

    # ─── 8. Bridge Narrative ──────────────────────────
    bridge_data = []
    bridge_narrative: list[str] = []
    if version.parent_version_id and prior_total is not None:
        parent_cats = (
            db.query(LineItem.category, func.sum(ForecastLineResult.p50).label("p50"))
            .join(LineItem)
            .filter(ForecastLineResult.version_id == version.parent_version_id)
            .group_by(LineItem.category)
            .all()
        )
        parent_map = {r.category: float(r.p50 or 0) for r in parent_cats}

        bridge_data.append({"name": "Prior Forecast", "value": round(prior_total, 2), "invisible": 0, "is_total": True})
        running = prior_total
        movements = []
        for cat in sorted(cat_totals.keys()):
            current_val = cat_totals[cat]["p50"]
            prior_val = parent_map.get(cat, 0)
            delta = current_val - prior_val
            if abs(delta) > 0.01:
                bridge_data.append({
                    "name": cat,
                    "value": round(delta, 2),
                    "invisible": round(min(running, running + delta), 2),
                    "is_total": False,
                })
                running += delta
                direction = "up" if delta > 0 else "down"
                movements.append(f"{cat} {direction} {formatCurrency(abs(delta))}")
        bridge_data.append({"name": "Current", "value": round(total_p50, 2), "invisible": 0, "is_total": True})

        if movements:
            bridge_narrative = movements[:5]

    # ─── 9. Monthly time series ───────────────────────
    time_series = envelope["time_series"]

    # Company-defined KPI formulas. Never allowed to break the dashboard.
    try:
        custom_kpis = evaluate_kpis(db, version)
    except Exception:
        logger.exception("Custom KPI evaluation failed for version %s", version.id)
        custom_kpis = []

    # ─── 10. Accuracy KPIs ────────────────────────────
    accuracy_kpis: dict[str, Any] = {"avg_mape": 0, "avg_bias": 0, "bias_direction": "neutral", "hit_rate": 0, "comparisons": 0}
    if actuals_map:
        mape_values: list[float] = []
        sum_forecast, sum_actual = 0.0, 0.0
        hit_count = 0
        for r in all_results:
            act_val = actuals_map.get((r.line_item_id, r.period))
            if act_val and abs(act_val) > 1e-10:
                mape_values.append(abs(r.p50 - act_val) / abs(act_val) * 100)
                sum_forecast += float(r.p50)
                sum_actual += float(act_val)
                if r.p10 is not None and r.p90 is not None and r.p10 <= act_val <= r.p90:
                    hit_count += 1
        if mape_values and abs(sum_actual) > 1e-10:
            agg_bias = (sum_forecast - sum_actual) / abs(sum_actual) * 100
            accuracy_kpis = {
                "avg_mape": round(sum(mape_values) / len(mape_values), 2),
                "avg_bias": round(agg_bias, 2),
                "bias_direction": "over" if agg_bias > 1 else "under" if agg_bias < -1 else "neutral",
                "bias_dollar": round(sum_forecast - sum_actual, 2),
                "hit_rate": round(hit_count / len(mape_values) * 100, 1),
                "comparisons": len(mape_values),
            }

    return PanelDataResponse(
        panel_type="executive_dashboard",
        title=f"CFO Dashboard: {version.name}",
        data={
            "version": {
                "id": version.id,
                "name": version.name,
                "status": version.status,
                "base_period": version.base_period,
                "horizon_months": version.horizon_months,
                "created_at": version.created_at.isoformat() if version.created_at else None,
            },
            "position": {
                "total_p50": round(total_p50, 2),
                "total_p10": round(total_p10, 2),
                "total_p90": round(total_p90, 2),
                "downside_risk": round(downside_risk, 2),
                "upside_opportunity": round(upside_opportunity, 2),
                "risk_basis": "net_income",
                "net_income_p50": round(envelope["net_income_p50"], 2),
                "risk_by_type": envelope["risk_by_type"],
                "risk_method": envelope["risk_method"],
                "prior_total": round(prior_total, 2) if prior_total else None,
                "delta_vs_prior": round(delta_vs_prior, 2) if delta_vs_prior is not None else None,
                "delta_pct": round(delta_pct, 1) if delta_pct is not None else None,
                "override_count": version.override_count,
            },
            "review_progress": review_progress,
            "priority_items": priority_items,
            "risk_opportunity": risk_opportunity,
            "insights": insights,
            "override_summary": override_summary,
            "driver_summary": driver_summary,
            "bridge_data": bridge_data,
            "bridge_narrative": bridge_narrative,
            "accuracy": accuracy_kpis,
            "time_series": time_series,
            "custom_kpis": custom_kpis,
            "version_business_unit_id": version.business_unit_id,
        },
    )


