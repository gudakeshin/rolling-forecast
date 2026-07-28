"""Dashboard and analytics endpoints for side panel views."""

import logging
from datetime import datetime, timezone
from typing import Any

import numpy as np

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.api.auth import get_current_user
from app.models.user import User
from app.models.forecast import ForecastVersion, ForecastLineResult, ModelMetadata
from app.models.line_item import LineItem, LineItemDependency
from app.models.actuals import ActualsRecord
from app.models.override import Override
from app.schemas.forecast import PanelDataResponse
from app.services.permissions import line_item_scope_filter, require_permission, scoped_line_items

# Scoring lives in services.confidence; remediation stays on the skill module.
from app.services.confidence import (
    compute_confidence_score as _compute_confidence_score,
    classify_confidence as _classify_confidence,
)
from app.domain.skills.generate_baseline import _generate_remediation

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/panel", tags=["dashboard"])


def _extract_exog_payload(parameters: dict | None) -> dict | None:
    """Return a compact exog payload for review UI, if present."""
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


def _scoped_results_query(db: Session, user: User, version_id: str):
    """ForecastLineResult query joined to LineItem, restricted by BU scope."""
    q = (
        db.query(ForecastLineResult)
        .join(LineItem)
        .filter(ForecastLineResult.version_id == version_id)
    )
    return line_item_scope_filter(q, user, LineItem)


def formatCurrency(value: float) -> str:
    """Format a numeric value as a currency string for backend narratives."""
    if abs(value) >= 1_000_000:
        return f"${value / 1_000_000:,.1f}M"
    if abs(value) >= 1_000:
        return f"${value / 1_000:,.0f}K"
    return f"${value:,.0f}"


# ──────────────────────────────────────────────────
# Business Driver Context Builder
# ──────────────────────────────────────────────────


def _preload_driver_context(
    db: Session,
    version_id: str,
    line_item_ids: list[int],
) -> dict[str, Any]:
    """Batch-load dependency / forecast / override / driver data for review items.

    Avoids the prior ~4 queries per line item inside `_build_driver_context`.
    """
    empty: dict[str, Any] = {
        "deps_by_dependent": {},
        "deps_by_source": {},
        "line_items_by_id": {},
        "forecast_totals": {},
        "overrides_by_li": {},
        "driver_rows": [],
    }
    if not line_item_ids:
        return empty

    deps = (
        db.query(LineItemDependency)
        .filter(
            (LineItemDependency.dependent_item_id.in_(line_item_ids))
            | (LineItemDependency.source_item_id.in_(line_item_ids))
        )
        .all()
    )
    deps_by_dependent: dict[int, list[LineItemDependency]] = {}
    deps_by_source: dict[int, list[LineItemDependency]] = {}
    related_ids: set[int] = set(line_item_ids)
    for dep in deps:
        deps_by_dependent.setdefault(dep.dependent_item_id, []).append(dep)
        deps_by_source.setdefault(dep.source_item_id, []).append(dep)
        related_ids.add(dep.source_item_id)
        related_ids.add(dep.dependent_item_id)

    line_items_by_id = {
        li.id: li
        for li in db.query(LineItem).filter(LineItem.id.in_(related_ids)).all()
    }

    forecast_totals = {
        li_id: float(total or 0)
        for li_id, total in (
            db.query(ForecastLineResult.line_item_id, func.sum(ForecastLineResult.p50))
            .filter(
                ForecastLineResult.version_id == version_id,
                ForecastLineResult.line_item_id.in_(related_ids),
            )
            .group_by(ForecastLineResult.line_item_id)
            .all()
        )
    }

    overrides_by_li: dict[int, list[Override]] = {}
    for ov in (
        db.query(Override)
        .filter(
            Override.version_id == version_id,
            Override.line_item_id.in_(line_item_ids),
            Override.status == "active",
        )
        .order_by(Override.created_at.desc())
        .all()
    ):
        bucket = overrides_by_li.setdefault(ov.line_item_id, [])
        if len(bucket) < 5:
            bucket.append(ov)

    driver_rows: list[Any] = []
    try:
        from app.models.driver_input import DriverInput

        driver_rows = (
            db.query(DriverInput)
            .filter(DriverInput.version_id == version_id)
            .all()
        )
    except Exception:
        driver_rows = []

    return {
        "deps_by_dependent": deps_by_dependent,
        "deps_by_source": deps_by_source,
        "line_items_by_id": line_items_by_id,
        "forecast_totals": forecast_totals,
        "overrides_by_li": overrides_by_li,
        "driver_rows": driver_rows,
    }


def _build_driver_context(
    li: LineItem,
    group: list[ForecastLineResult],
    actuals_map: dict[tuple[int, str], float],
    db: Session,
    version_id: str,
    cache: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a business driver narrative for a line item.

    Prefer a preloaded `cache` from `_preload_driver_context` when building
    many items; falls back to per-item queries for single-item call sites.
    """
    context: dict[str, Any] = {
        "dependencies": [],
        "actuals_trend": None,
        "driver_inputs": [],
        "active_overrides": [],
        "narrative": "",
    }

    if not li:
        return context

    if cache is None:
        cache = _preload_driver_context(db, version_id, [li.id])

    # ── 1. Dependencies: what P&L lines feed into this item ───
    deps = cache["deps_by_dependent"].get(li.id, [])
    line_items_by_id: dict[int, LineItem] = cache["line_items_by_id"]
    forecast_totals: dict[int, float] = cache["forecast_totals"]
    for dep in deps:
        source = line_items_by_id.get(dep.source_item_id)
        if source:
            context["dependencies"].append({
                "name": source.name,
                "category": source.category,
                "relationship": dep.relationship_type,
                "weight": dep.weight,
                "forecast_total": round(forecast_totals.get(source.id, 0.0), 2),
            })

    # Also show what this item feeds into (dependents)
    downstream_names = []
    for dep in cache["deps_by_source"].get(li.id, []):
        dependent = line_items_by_id.get(dep.dependent_item_id)
        if dependent:
            downstream_names.append(dependent.name)

    # ── 2. Actuals trend: recent historical pattern ───────────
    li_actuals = sorted(
        [(k[1], v) for k, v in actuals_map.items() if k[0] == li.id],
        key=lambda x: x[0],
    )
    if len(li_actuals) >= 2:
        recent_vals = [v for _, v in li_actuals[-6:]]
        recent_periods = [p for p, _ in li_actuals[-6:]]

        # MoM growth
        if len(recent_vals) >= 2 and recent_vals[-2] != 0:
            mom_growth = (recent_vals[-1] - recent_vals[-2]) / abs(recent_vals[-2]) * 100
        else:
            mom_growth = None

        # Average over recent window
        avg_recent = sum(recent_vals) / len(recent_vals) if recent_vals else 0

        # YoY if enough data (12+ months)
        yoy_growth = None
        if len(li_actuals) >= 13:
            current_val = li_actuals[-1][1]
            year_ago_val = li_actuals[-13][1]
            if year_ago_val != 0:
                yoy_growth = (current_val - year_ago_val) / abs(year_ago_val) * 100

        # Direction
        if len(recent_vals) >= 3:
            first_half = sum(recent_vals[:len(recent_vals)//2]) / max(1, len(recent_vals)//2)
            second_half = sum(recent_vals[len(recent_vals)//2:]) / max(1, len(recent_vals) - len(recent_vals)//2)
            if first_half != 0:
                direction_pct = (second_half - first_half) / abs(first_half) * 100
                direction = "upward" if direction_pct > 5 else "downward" if direction_pct < -5 else "flat"
            else:
                direction = "flat"
                direction_pct = 0
        else:
            direction = "insufficient"
            direction_pct = 0

        context["actuals_trend"] = {
            "periods": recent_periods,
            "values": [round(v, 2) for v in recent_vals],
            "direction": direction,
            "direction_pct": round(direction_pct, 1),
            "mom_growth_pct": round(mom_growth, 1) if mom_growth is not None else None,
            "yoy_growth_pct": round(yoy_growth, 1) if yoy_growth is not None else None,
            "recent_avg": round(avg_recent, 2),
            "last_actual": round(recent_vals[-1], 2),
            "last_period": recent_periods[-1],
        }

    # ── 3. Driver inputs for this line item ───────────────────
    # Values are keyed by line_item_id (REST/UI) or field name (skill). Match
    # the dict key first — field_data["line_item_id"] is absent on legacy rows.
    for di in cache.get("driver_rows") or []:
        if di.values and isinstance(di.values, dict):
            for field_name, field_data in di.values.items():
                payload = field_data if isinstance(field_data, dict) else {"value": field_data}
                key_matches = str(field_name).isdigit() and int(field_name) == li.id
                nested_matches = payload.get("line_item_id") == li.id
                if not (key_matches or nested_matches):
                    continue
                context["driver_inputs"].append({
                    "business_unit": di.business_unit,
                    "field": field_name,
                    "value": payload.get("value"),
                    "reason": payload.get("reason", ""),
                    "prior_value": payload.get("prior_value"),
                    "submitted_at": di.submitted_at.isoformat() if di.submitted_at else None,
                })

    # ── 4. Active overrides ───────────────────────────────────
    for ov in cache.get("overrides_by_li", {}).get(li.id, []):
        context["active_overrides"].append({
            "period": ov.period,
            "original_value": round(ov.original_model_value, 2),
            "override_value": round(ov.override_value, 2),
            "reason": ov.reason,
            "delta_pct": round(
                (ov.override_value - ov.original_model_value)
                / (abs(ov.original_model_value) + 1e-10) * 100,
                1,
            ),
        })

    # ── 5. Build human-readable narrative ─────────────────────
    narrative_parts: list[str] = []

    # Dependency narrative
    if context["dependencies"]:
        dep_names = [d["name"] for d in context["dependencies"]]
        if li.is_calculated and li.formula:
            narrative_parts.append(
                f"{li.name} is a calculated item ({li.formula}) driven by: {', '.join(dep_names)}."
            )
        elif len(dep_names) == 1:
            narrative_parts.append(
                f"{li.name} is primarily driven by {dep_names[0]}."
            )
        else:
            narrative_parts.append(
                f"{li.name} is composed of {len(dep_names)} driver lines: {', '.join(dep_names[:4])}"
                + (f" and {len(dep_names) - 4} more" if len(dep_names) > 4 else "")
                + "."
            )

    # Downstream impact
    if downstream_names:
        narrative_parts.append(
            f"Changes to {li.name} will cascade to: {', '.join(downstream_names[:3])}"
            + (f" and {len(downstream_names) - 3} others" if len(downstream_names) > 3 else "")
            + "."
        )

    # Actuals trend narrative
    trend = context["actuals_trend"]
    if trend:
        if trend["direction"] == "upward":
            narrative_parts.append(
                f"Recent actuals show an upward trend (+{trend['direction_pct']:.0f}% over the last {len(trend['periods'])} months)."
            )
        elif trend["direction"] == "downward":
            narrative_parts.append(
                f"Recent actuals show a declining trend ({trend['direction_pct']:.0f}% over the last {len(trend['periods'])} months)."
            )
        else:
            narrative_parts.append(
                f"Actuals have been relatively flat over the last {len(trend['periods'])} months (avg: ${trend['recent_avg']:,.0f}/mo)."
            )

        if trend["yoy_growth_pct"] is not None:
            direction = "growth" if trend["yoy_growth_pct"] > 0 else "decline"
            narrative_parts.append(
                f"Year-over-year {direction} of {abs(trend['yoy_growth_pct']):.1f}%."
            )

        # Compare forecast to actuals trend
        if group:
            forecast_avg = sum(r.p50 for r in group) / len(group)
            if trend["recent_avg"] != 0:
                forecast_vs_actual = (forecast_avg - trend["recent_avg"]) / abs(trend["recent_avg"]) * 100
                if abs(forecast_vs_actual) > 10:
                    direction = "above" if forecast_vs_actual > 0 else "below"
                    narrative_parts.append(
                        f"The forecast average (${forecast_avg:,.0f}/mo) is {abs(forecast_vs_actual):.0f}% {direction} "
                        f"the recent actuals average (${trend['recent_avg']:,.0f}/mo)."
                    )

    # Driver input narrative
    if context["driver_inputs"]:
        for di in context["driver_inputs"][:2]:
            reason = di.get("reason")
            if reason:
                narrative_parts.append(
                    f"BU input ({di['business_unit']}): {reason}"
                )

    # Override narrative
    if context["active_overrides"]:
        total_overrides = len(context["active_overrides"])
        narrative_parts.append(
            f"{total_overrides} manual override(s) active. "
            f"Last override: {context['active_overrides'][0]['reason']}"
        )

    context["narrative"] = " ".join(narrative_parts) if narrative_parts else ""

    return context


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

    version = db.query(ForecastVersion).filter(ForecastVersion.id == version_id).first()
    if not version:
        raise HTTPException(status_code=404, detail="Forecast version not found")

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
    total_p10, total_p50, total_p90 = 0.0, 0.0, 0.0
    cat_totals: dict[str, dict[str, float]] = {}
    for r in all_results:
        if r.line_item and not r.line_item.is_subtotal:
            total_p10 += float(r.p10 or 0)
            total_p50 += float(r.p50 or 0)
            total_p90 += float(r.p90 or 0)
            cat = r.line_item.category
            if cat not in cat_totals:
                cat_totals[cat] = {"p10": 0, "p50": 0, "p90": 0}
            cat_totals[cat]["p10"] += float(r.p10 or 0)
            cat_totals[cat]["p50"] += float(r.p50 or 0)
            cat_totals[cat]["p90"] += float(r.p90 or 0)

    downside_risk = total_p50 - total_p10
    upside_opportunity = total_p90 - total_p50

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
    risk_opportunity = []
    for cat, totals in sorted(cat_totals.items(), key=lambda x: -abs(x[1]["p50"])):
        downside = totals["p50"] - totals["p10"]
        upside = totals["p90"] - totals["p50"]
        risk_opportunity.append({
            "category": cat,
            "forecast": round(totals["p50"], 2),
            "downside_risk": round(downside, 2),
            "upside_opportunity": round(upside, 2),
            "range_pct": round((totals["p90"] - totals["p10"]) / abs(totals["p50"]) * 100, 1) if abs(totals["p50"]) > 1e-10 else 0,
        })

    # ─── 6. Key Business Drivers & Assumptions ────────
    # Recent overrides (CFO needs to know what's been manually adjusted)
    recent_overrides = (
        db.query(Override, LineItem)
        .join(LineItem, Override.line_item_id == LineItem.id)
        .filter(Override.version_id == version_id, Override.status == "active")
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

    # Driver input submissions
    driver_inputs = db.query(DriverInput).filter(DriverInput.version_id == version_id).all()
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
            "detail": f"The P10 scenario is {formatCurrency(downside_risk)} below the base forecast, a {downside_risk / abs(total_p50) * 100:.0f}% gap. Key drivers: {', '.join(c['category'] for c in risk_opportunity[:3] if c['downside_risk'] > 0)}.",
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
    monthly_totals = (
        db.query(
            ForecastLineResult.period,
            func.sum(ForecastLineResult.p10).label("p10"),
            func.sum(ForecastLineResult.p50).label("p50"),
            func.sum(ForecastLineResult.p90).label("p90"),
        )
        .filter(ForecastLineResult.version_id == version_id)
        .group_by(ForecastLineResult.period)
        .order_by(ForecastLineResult.period)
        .all()
    )
    time_series = [{
        "period": r.period,
        "p10": round(float(r.p10 or 0), 2),
        "p50": round(float(r.p50 or 0), 2),
        "p90": round(float(r.p90 or 0), 2),
    } for r in monthly_totals]

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
        },
    )


# ──────────────────────────────────────────────────
# Enhanced Review Dashboard (with AI analysis & actions)
# ──────────────────────────────────────────────────


def _ai_analyze_item(
    r: ForecastLineResult,
    actuals_map: dict[tuple[int, str], float],
    category_stats: dict[str, dict],
    total_p50: float = 0.0,
) -> dict[str, Any]:
    """Run business-context-aware AI analysis on a single forecast line result.

    Produces:
    - recommendation: approve / review / flag / override / manual_input
    - reasoning: concise business-language explanation
    - risk_score: 0-100
    - actions: list of specific, actionable next steps the user can take
    - materiality: how material this item is to the total forecast (low/medium/high)
    - root_cause: classified root cause category for the issue

    Uses statistical heuristics weighted by business materiality so that
    a 10% error on a $5M item ranks higher than a 30% error on a $10K item.
    """
    issues: list[str] = []
    actions: list[dict[str, str]] = []  # {type, label, detail}
    risk = 0.0
    root_cause = "none"

    li = r.line_item
    li_name = li.name if li else "Unknown"
    cat = li.category if li else "Unknown"

    # ── Materiality: how much does this item matter? ───────────
    abs_p50 = abs(r.p50) if r.p50 else 0
    materiality = "low"
    materiality_weight = 1.0
    if total_p50 > 0:
        materiality_pct = abs_p50 / total_p50 * 100
        if materiality_pct > 5:
            materiality = "high"
            materiality_weight = 1.5  # Amplify risk for material items
        elif materiality_pct > 1:
            materiality = "medium"
            materiality_weight = 1.2
    elif abs_p50 > 500_000:
        materiality = "high"
        materiality_weight = 1.5
    elif abs_p50 > 100_000:
        materiality = "medium"
        materiality_weight = 1.2

    # ── 1. Zero / Average model → structural data issue ───────
    if r.model_type == "zero":
        return {
            "recommendation": "manual_input",
            "reasoning": f"{li_name} has no historical activity. A manual forecast is required — this cannot be modeled statistically.",
            "risk_score": 85.0,
            "actions": [
                {"type": "driver_input", "label": "Request BU estimate",
                 "detail": f"Ask the business unit owner to provide an expected value for {li_name}."},
                {"type": "override", "label": "Set manual value",
                 "detail": "Apply a manual override if you have a reasonable estimate."},
                {"type": "confirm_zero", "label": "Confirm zero",
                 "detail": "If this line item should genuinely be zero, approve as-is."},
            ],
            "materiality": materiality,
            "root_cause": "no_data",
        }

    if r.model_type == "average":
        return {
            "recommendation": "review",
            "reasoning": f"{li_name} has less than 12 months of history, so only a simple average could be used. The forecast lacks trend and seasonality adjustments.",
            "risk_score": 55.0 * materiality_weight,
            "actions": [
                {"type": "upload_data", "label": "Upload more history",
                 "detail": "Provide at least 12-24 months of actuals to enable ARIMA/Prophet/ETS modeling."},
                {"type": "driver_input", "label": "Collect business assumptions",
                 "detail": f"Get expected growth rate or known changes for {li_name} from the BU owner."},
                {"type": "override", "label": "Override with adjusted value",
                 "detail": "Apply a growth/decline adjustment on top of the average if you know the direction."},
            ],
            "materiality": materiality,
            "root_cause": "sparse_data",
        }

    # ── 2. Confidence score analysis ──────────────────────────
    if r.confidence_score < 30:
        risk += 30 * materiality_weight
        issues.append(f"Confidence is very low at {r.confidence_score:.0f}/100")
        root_cause = "low_confidence"
    elif r.confidence_score < 50:
        risk += 15 * materiality_weight
        issues.append(f"Confidence is below threshold at {r.confidence_score:.0f}/100")
        root_cause = "low_confidence"

    # ── 3. Model fit (MAPE) ───────────────────────────────────
    if r.model_mape is not None:
        if r.model_mape > 25:
            risk += 25 * materiality_weight
            issues.append(
                f"The {r.model_type or 'statistical'} model has a {r.model_mape:.1f}% average error rate "
                f"— for a {materiality}-materiality item, this level of error translates to "
                f"±${abs_p50 * r.model_mape / 100:,.0f} potential variance"
            )
            actions.append({
                "type": "switch_model", "label": "Try alternative models",
                "detail": f"Re-run forecast for {li_name} with 'auto' model selection to test ARIMA, Prophet, ETS, and Linear against each other.",
            })
            root_cause = root_cause or "poor_model_fit"
        elif r.model_mape > 15:
            risk += 12 * materiality_weight
            issues.append(
                f"Model error is moderate ({r.model_mape:.1f}% MAPE) — "
                f"roughly ±${abs_p50 * r.model_mape / 100:,.0f} potential variance"
            )

    # ── 4. Prediction interval width ──────────────────────────
    if r.p10 is not None and r.p90 is not None and r.p50 != 0:
        band_pct = abs(r.p90 - r.p10) / (abs(r.p50) + 1e-10) * 100
        band_abs = abs(r.p90 - r.p10)
        if band_pct > 80:
            risk += 20 * materiality_weight
            issues.append(
                f"Very high uncertainty: the P10-P90 range spans ${band_abs:,.0f} "
                f"({band_pct:.0f}% of the point forecast). "
                f"Downside risk is ${abs(r.p50 - r.p10):,.0f} below base case"
            )
            actions.append({
                "type": "driver_input", "label": "Narrow with business input",
                "detail": "Collect specific assumptions from BU owners to constrain the forecast range.",
            })
            root_cause = root_cause or "high_uncertainty"
        elif band_pct > 50:
            risk += 10 * materiality_weight
            issues.append(
                f"Moderate uncertainty: P10-P90 range spans ${band_abs:,.0f} ({band_pct:.0f}% of forecast)"
            )

    # ── 5. Actual vs forecast deviation ───────────────────────
    li_id = r.line_item_id
    actual_val = actuals_map.get((li_id, r.period))
    if actual_val is not None and actual_val != 0:
        deviation_pct = (r.p50 - actual_val) / abs(actual_val) * 100
        deviation_abs = r.p50 - actual_val
        direction = "above" if deviation_abs > 0 else "below"
        if abs(deviation_pct) > 30:
            risk += 18 * materiality_weight
            issues.append(
                f"Forecast is {abs(deviation_pct):.0f}% {direction} the comparable actuals period "
                f"(${deviation_abs:+,.0f} variance). "
                + ("This may indicate an optimistic projection." if deviation_abs > 0
                   else "This may indicate unaccounted headwinds or conservative modeling.")
            )
            actions.append({
                "type": "investigate", "label": "Validate business change",
                "detail": f"Confirm whether there is a known business driver "
                          f"(contract win/loss, pricing change, restructuring) that explains the "
                          f"{abs(deviation_pct):.0f}% {'increase' if deviation_abs > 0 else 'decrease'}.",
            })
            root_cause = root_cause or "forecast_drift"
        elif abs(deviation_pct) > 15:
            risk += 8 * materiality_weight
            issues.append(
                f"Forecast trends {abs(deviation_pct):.0f}% {direction} comparable actuals "
                f"(${deviation_abs:+,.0f})"
            )

    # ── 6. Override impact ────────────────────────────────────
    if r.is_overridden and r.override_value is not None and r.p50 != 0:
        override_delta = r.override_value - r.p50
        override_pct = abs(override_delta) / (abs(r.p50) + 1e-10) * 100
        if override_pct > 20:
            risk += 10
            issues.append(
                f"Manual override shifts the forecast by ${override_delta:+,.0f} ({override_pct:.0f}%). "
                "Verify the override is still valid and reflects current assumptions."
            )
            actions.append({
                "type": "review_override", "label": "Validate override",
                "detail": "Check whether the original override rationale still applies. "
                          "If business conditions have changed, consider removing or updating the override.",
            })
            root_cause = root_cause or "override_impact"

    # ── 7. Category outlier ───────────────────────────────────
    if cat in category_stats:
        cat_mean = category_stats[cat].get("mean_p50", 0)
        cat_std = category_stats[cat].get("std_p50", 1)
        if cat_std > 0 and cat_mean != 0:
            z_score = (r.p50 - cat_mean) / cat_std
            if abs(z_score) > 2.5:
                risk += 10
                direction = "higher" if z_score > 0 else "lower"
                issues.append(
                    f"This item is significantly {direction} than peers in {cat} "
                    f"(z-score: {z_score:.1f}). It may be an outlier or a genuinely different business line."
                )
                actions.append({
                    "type": "compare_peers", "label": "Compare with category peers",
                    "detail": f"Open the forecast comparison view to see how {li_name} "
                              f"differs from other items in {cat}.",
                })
                root_cause = root_cause or "category_outlier"

    # ── Determine recommendation & build final reasoning ──────
    risk = min(risk, 100)

    if not issues:
        recommendation = "approve"
        reasoning = (
            f"{li_name} passes all quality checks — "
            f"confidence {r.confidence_score:.0f}/100, "
            + (f"MAPE {r.model_mape:.1f}%, " if r.model_mape else "")
            + "consistent with historical patterns."
        )
        actions = [{"type": "approve", "label": "Auto-approve", "detail": "No action needed."}]
    elif risk <= 25:
        recommendation = "approve"
        reasoning = (
            f"{li_name}: Minor observations within acceptable range. "
            + " ".join(issues)
        )
        actions = actions or [{"type": "approve", "label": "Approve as-is", "detail": "Acceptable risk level."}]
    elif risk <= 50:
        recommendation = "review"
        reasoning = " | ".join(issues)
        if not actions:
            actions = [{
                "type": "override", "label": "Adjust forecast",
                "detail": f"Apply a manual override to {li_name} if the model projection doesn't match your business expectation.",
            }]
    elif risk <= 70:
        recommendation = "flag"
        reasoning = " | ".join(issues)
        if not actions:
            actions = [
                {"type": "override", "label": "Override with manual estimate",
                 "detail": f"Replace the model output for {li_name} with a business-informed estimate."},
                {"type": "driver_input", "label": "Request BU input",
                 "detail": "Ask the business unit owner for specific assumptions to improve this forecast."},
            ]
    else:
        recommendation = "override"
        reasoning = " | ".join(issues)
        if not actions:
            actions = [
                {"type": "override", "label": "Override required",
                 "detail": f"The statistical model is unreliable for {li_name}. A manual override or BU input is needed."},
                {"type": "driver_input", "label": "Collect driver assumptions",
                 "detail": "Request specific volume, pricing, or contract data from the BU owner."},
                {"type": "switch_model", "label": "Try different model",
                 "detail": "Re-run with a different algorithm to see if fit improves."},
            ]

    return {
        "recommendation": recommendation,
        "reasoning": reasoning,
        "risk_score": round(risk, 1),
        "actions": actions,
        "materiality": materiality,
        "root_cause": root_cause or "none",
    }


@router.get("/review-dashboard/{version_id}", response_model=PanelDataResponse)
async def get_review_dashboard(
    version_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Enhanced review dashboard with AI analysis, buckets, and interactive actions."""
    version = db.query(ForecastVersion).filter(ForecastVersion.id == version_id).first()
    if not version:
        raise HTTPException(status_code=404, detail="Forecast version not found")

    results = (
        _scoped_results_query(db, current_user, version_id)
        .order_by(ForecastLineResult.confidence_score.asc())
        .all()
    )

    # Build actuals lookup for AI analysis
    actuals_map: dict[tuple[int, str], float] = {}
    if version.actuals_dataset_id:
        actuals = (
            db.query(ActualsRecord)
            .filter(ActualsRecord.dataset_id == version.actuals_dataset_id)
            .all()
        )
        for a in actuals:
            actuals_map[(a.line_item_id, a.period)] = a.value

    # Build category statistics for outlier detection
    category_stats: dict[str, dict] = {}
    cat_values: dict[str, list[float]] = {}
    for r in results:
        cat = r.line_item.category if r.line_item else "Unknown"
        cat_values.setdefault(cat, []).append(r.p50)
    for cat, vals in cat_values.items():
        import numpy as np
        arr = np.array(vals)
        category_stats[cat] = {
            "mean_p50": float(np.mean(arr)),
            "std_p50": float(np.std(arr)) if len(arr) > 1 else 0,
        }

    # --- AI Analysis: group by line_item for a summarized review view ---
    # Instead of showing every period individually (overwhelming), we aggregate
    # per line item and only surface the worst-period for flagged items.
    line_item_groups: dict[int, list[ForecastLineResult]] = {}
    for r in results:
        line_item_groups.setdefault(r.line_item_id, []).append(r)

    # Compute total P50 for materiality weighting
    total_p50_sum = sum(
        abs(r.p50) for r in results
        if r.line_item and not r.line_item.is_subtotal
    ) or 1.0

    ai_approved_items = []     # AI says approve
    ai_review_items = []       # AI says needs human review
    ai_flagged_items = []      # AI says flag / override needed
    already_reviewed_items = []  # Human already reviewed

    driver_cache = _preload_driver_context(db, version_id, list(line_item_groups.keys()))

    metadata_by_result_id: dict[str, dict] = {}
    result_ids = [str(r.id) for r in results]
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

    for li_id, group in line_item_groups.items():
        # Pick the worst-case period for this line item
        worst = min(group, key=lambda x: x.confidence_score)
        ai_result = _ai_analyze_item(worst, actuals_map, category_stats, total_p50=total_p50_sum)
        exog_payload = _extract_exog_payload(metadata_by_result_id.get(str(worst.id)))

        # Persist AI analysis
        for r in group:
            r.ai_recommendation = ai_result["recommendation"]
            r.ai_reasoning = ai_result["reasoning"]
            r.ai_risk_score = ai_result["risk_score"]

        li = worst.line_item

        # Build business driver context (batched via driver_cache)
        driver_ctx = _build_driver_context(
            li, group, actuals_map, db, version_id, cache=driver_cache,
        )

        item = {
            "id": worst.id,
            "line_item_id": li_id,
            "line_item_name": li.name if li else "Unknown",
            "account_code": li.account_code if li else None,
            "category": li.category if li else "Unknown",
            "subcategory": li.subcategory if li else None,
            "business_unit": li.business_unit if li else None,
            "period_count": len(group),
            "worst_period": worst.period,
            "p50_range": f"${min(r.p50 for r in group):,.0f} – ${max(r.p50 for r in group):,.0f}",
            "total_p50": round(sum(r.p50 for r in group), 2),
            "avg_p50": round(sum(r.p50 for r in group) / len(group), 2),
            "avg_confidence": round(sum(r.confidence_score for r in group) / len(group), 1),
            "min_confidence": round(worst.confidence_score, 1),
            "confidence_level": worst.confidence_level,
            "model_type": worst.model_type,
            "model_mape": round(worst.model_mape, 1) if worst.model_mape else None,
            "is_overridden": any(r.is_overridden for r in group),
            "override_count": sum(1 for r in group if r.is_overridden),
            # AI analysis
            "ai_recommendation": ai_result["recommendation"],
            "ai_reasoning": ai_result["reasoning"],
            "ai_risk_score": ai_result["risk_score"],
            "ai_actions": ai_result.get("actions", []),
            "materiality": ai_result.get("materiality", "low"),
            "root_cause": ai_result.get("root_cause", "none"),
            # Business driver context
            "driver_context": {
                "narrative": driver_ctx["narrative"],
                "dependencies": driver_ctx["dependencies"],
                "actuals_trend": driver_ctx["actuals_trend"],
                "driver_inputs": driver_ctx["driver_inputs"],
                "active_overrides": driver_ctx["active_overrides"],
            },
            # Review status (human)
            "review_status": worst.review_status,
            "review_comment": worst.review_comment,
            "reviewed_by": worst.reviewed_by,
            "exog_used": bool(exog_payload),
            "exog": exog_payload,
        }

        if worst.review_status in ("approved", "rejected"):
            already_reviewed_items.append(item)
        elif ai_result["recommendation"] == "approve":
            ai_approved_items.append(item)
        elif ai_result["recommendation"] in ("flag", "override"):
            ai_flagged_items.append(item)
        else:
            ai_review_items.append(item)

    db.commit()  # Persist AI analysis results

    # Sort: flagged by risk desc, review by risk desc, approved by name
    ai_flagged_items.sort(key=lambda x: -x["ai_risk_score"])
    ai_review_items.sort(key=lambda x: -x["ai_risk_score"])
    ai_approved_items.sort(key=lambda x: x["line_item_name"])

    # Historical context: confidence trend over recent versions
    recent_versions = (
        db.query(ForecastVersion)
        .filter(ForecastVersion.id != version_id)
        .order_by(ForecastVersion.created_at.desc())
        .limit(5)
        .all()
    )

    confidence_trend = []
    for v in reversed(recent_versions):
        avg_conf = (
            db.query(func.avg(ForecastLineResult.confidence_score))
            .filter(ForecastLineResult.version_id == v.id)
            .scalar()
        )
        confidence_trend.append({
            "version": v.name[:15],
            "avg_confidence": round(float(avg_conf or 0), 1),
        })

    cur_avg = (
        db.query(func.avg(ForecastLineResult.confidence_score))
        .filter(ForecastLineResult.version_id == version_id)
        .scalar()
    )
    confidence_trend.append({
        "version": version.name[:15],
        "avg_confidence": round(float(cur_avg or 0), 1),
    })

    # Category breakdown for flagged
    category_flags: dict[str, int] = {}
    for item in ai_flagged_items + ai_review_items:
        cat = item["category"]
        category_flags[cat] = category_flags.get(cat, 0) + 1
    category_flag_chart = [
        {"category": k, "count": v}
        for k, v in sorted(category_flags.items(), key=lambda x: -x[1])
    ]

    total_items = len(line_item_groups)
    return PanelDataResponse(
        panel_type="review_dashboard",
        title=f"Review Dashboard: {version.name}",
        data={
            "version": {
                "id": version.id,
                "name": version.name,
                "status": version.status,
            },
            "buckets": {
                "ai_approved": {
                    "items": ai_approved_items,
                    "total": len(ai_approved_items),
                    "description": "AI analysis suggests these items can be auto-approved",
                },
                "needs_review": {
                    "items": ai_review_items,
                    "total": len(ai_review_items),
                    "description": "AI found moderate concerns — human review recommended",
                },
                "flagged": {
                    "items": ai_flagged_items,
                    "total": len(ai_flagged_items),
                    "description": "AI flagged significant issues — action required",
                },
                "already_reviewed": {
                    "items": already_reviewed_items,
                    "total": len(already_reviewed_items),
                    "description": "Already reviewed by a human",
                },
            },
            "confidence_trend": confidence_trend,
            "category_flag_chart": category_flag_chart,
            "summary": {
                "total_line_items": total_items,
                "ai_approved_count": len(ai_approved_items),
                "needs_review_count": len(ai_review_items),
                "flagged_count": len(ai_flagged_items),
                "already_reviewed_count": len(already_reviewed_items),
                "ai_analysis_complete": True,
            },
        },
    )


# ──────────────────────────────────────────────────
# Review Actions (per-item and batch)
# ──────────────────────────────────────────────────


class ReviewItemRequest(BaseModel):
    """Request to review a single forecast line item."""
    item_id: str  # ForecastLineResult.id (worst-period representative)
    action: str   # "approve" | "reject" | "flag"
    comment: str | None = None


class BatchReviewRequest(BaseModel):
    """Batch-review multiple items at once."""
    version_id: str
    item_ids: list[str]  # ForecastLineResult IDs to approve
    action: str   # "approve" | "reject"
    comment: str | None = None


class AcceptAIRequest(BaseModel):
    """Accept all AI-approved items at once."""
    version_id: str


@router.post("/review-item")
async def review_item(
    request: ReviewItemRequest,
    current_user: User = Depends(require_permission("review")),
    db: Session = Depends(get_db),
):
    """Approve, reject, or flag a single line item (applies to all its periods).

    Note: Line-level review SoD is deliberately not hard-blocked here.
    Segregation of duties is enforced where it matters — approvals.decide —
    so creators can still triage AI recommendations on their own draft.
    """
    from app.services.audit import record_audit

    result = db.query(ForecastLineResult).filter(
        ForecastLineResult.id == request.item_id
    ).first()
    if not result:
        raise HTTPException(status_code=404, detail="Forecast line result not found")

    # Apply to ALL periods for this line item in this version
    all_periods = (
        db.query(ForecastLineResult)
        .filter(
            ForecastLineResult.version_id == result.version_id,
            ForecastLineResult.line_item_id == result.line_item_id,
        )
        .all()
    )

    now = datetime.now(timezone.utc)
    for r in all_periods:
        r.review_status = request.action
        r.review_comment = request.comment
        r.reviewed_by = current_user.id
        r.reviewed_at = now

    record_audit(
        db,
        action=f"review.{request.action}",
        entity_type="forecast_line_result",
        entity_id=request.item_id,
        actor_id=current_user.id,
        actor_username=current_user.username,
        details={"version_id": result.version_id, "comment": request.comment},
    )
    db.commit()

    return {
        "success": True,
        "message": f"{request.action.title()}d {len(all_periods)} period(s) for line item",
        "affected_count": len(all_periods),
    }


@router.post("/batch-review")
async def batch_review(
    request: BatchReviewRequest,
    current_user: User = Depends(require_permission("review")),
    db: Session = Depends(get_db),
):
    """Batch approve/reject multiple line items at once."""
    from app.services.audit import record_audit

    now = datetime.now(timezone.utc)
    total_affected = 0

    for item_id in request.item_ids:
        result = db.query(ForecastLineResult).filter(
            ForecastLineResult.id == item_id
        ).first()
        if not result:
            continue

        all_periods = (
            db.query(ForecastLineResult)
            .filter(
                ForecastLineResult.version_id == result.version_id,
                ForecastLineResult.line_item_id == result.line_item_id,
            )
            .all()
        )

        for r in all_periods:
            r.review_status = request.action
            r.review_comment = request.comment
            r.reviewed_by = current_user.id
            r.reviewed_at = now

        total_affected += len(all_periods)

    record_audit(
        db,
        action=f"review.batch_{request.action}",
        entity_type="forecast_version",
        entity_id=request.version_id,
        actor_id=current_user.id,
        actor_username=current_user.username,
        details={
            "item_ids": request.item_ids,
            "action": request.action,
            "periods_affected": total_affected,
        },
    )
    db.commit()

    return {
        "success": True,
        "message": f"{request.action.title()}d {len(request.item_ids)} line items ({total_affected} periods)",
        "items_reviewed": len(request.item_ids),
        "periods_affected": total_affected,
    }


@router.post("/accept-ai-recommendations")
async def accept_ai_recommendations(
    request: AcceptAIRequest,
    current_user: User = Depends(require_permission("review")),
    db: Session = Depends(get_db),
):
    """Accept all AI-approved items in one click."""
    from app.services.audit import record_audit

    now = datetime.now(timezone.utc)

    ai_approved = (
        db.query(ForecastLineResult)
        .filter(
            ForecastLineResult.version_id == request.version_id,
            ForecastLineResult.ai_recommendation == "approve",
            ForecastLineResult.review_status.is_(None),
        )
        .all()
    )

    for r in ai_approved:
        r.review_status = "approved"
        r.review_comment = "Auto-approved based on AI recommendation"
        r.reviewed_by = current_user.id
        r.reviewed_at = now

    record_audit(
        db,
        action="review.accept_ai",
        entity_type="forecast_version",
        entity_id=request.version_id,
        actor_id=current_user.id,
        actor_username=current_user.username,
        details={"approved_count": len(ai_approved)},
    )
    db.commit()

    return {
        "success": True,
        "message": f"Auto-approved {len(ai_approved)} line-periods based on AI analysis",
        "approved_count": len(ai_approved),
    }


# ──────────────────────────────────────────────────
# Accuracy Tracking
# ──────────────────────────────────────────────────

@router.get("/accuracy-tracking/{version_id}", response_model=PanelDataResponse)
async def get_accuracy_tracking(
    version_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Accuracy tracking from vintage forecast_accuracy_records (fallback: live join)."""
    from app.models.fx import ForecastAccuracyRecord

    version = db.query(ForecastVersion).filter(ForecastVersion.id == version_id).first()
    if not version:
        raise HTTPException(status_code=404, detail="Forecast version not found")

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
        "total_comparisons": len(accuracy_items),
        "source": "vintage" if vintage_rows else "live",
    }

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
        },
    )


# ──────────────────────────────────────────────────
# Driver Input Form Data
# ──────────────────────────────────────────────────

@router.get("/driver-inputs/{version_id}", response_model=PanelDataResponse)
async def get_driver_inputs(
    version_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get driver input form data with model-suggested values."""
    from app.models.driver_input import DriverInput, DriverFormConfig

    version = db.query(ForecastVersion).filter(ForecastVersion.id == version_id).first()
    if not version:
        raise HTTPException(status_code=404, detail="Forecast version not found")

    # Get existing driver inputs
    inputs = (
        db.query(DriverInput)
        .filter(DriverInput.version_id == version_id)
        .order_by(DriverInput.business_unit, DriverInput.submitted_at.desc())
        .all()
    )

    items = []
    for di in inputs:
        items.append({
            "id": di.id,
            "business_unit": di.business_unit,
            "values": di.values,
            "status": di.status,
            "is_late": di.is_late,
            "submitted_at": di.submitted_at.isoformat() if di.submitted_at else None,
            "reviewed_by": di.reviewed_by,
            "review_comments": di.review_comments,
        })

    # Get form configs for driver input structure
    form_configs = db.query(DriverFormConfig).filter(DriverFormConfig.is_active == True).all()
    forms = [{
        "id": fc.id,
        "business_unit": fc.business_unit,
        "name": fc.name,
        "description": fc.description,
        "fields_schema": fc.fields_schema,
        "soft_deadline_days": fc.soft_deadline_days,
        "hard_deadline_days": fc.hard_deadline_days,
    } for fc in form_configs]

    # Get line items available for driver input
    line_items = (
        scoped_line_items(db, current_user, LineItem.is_calculated == False)  # noqa: E712
        .order_by(LineItem.category, LineItem.display_order)
        .all()
    )
    li_ids = [li.id for li in line_items]

    # Batch first-period forecast + latest actual (avoid 2 queries per line item)
    first_flr_by_li: dict[int, ForecastLineResult] = {}
    last_actual_by_li: dict[int, ActualsRecord] = {}
    if li_ids:
        for flr in (
            db.query(ForecastLineResult)
            .filter(
                ForecastLineResult.version_id == version_id,
                ForecastLineResult.line_item_id.in_(li_ids),
            )
            .order_by(ForecastLineResult.period)
            .all()
        ):
            if flr.line_item_id not in first_flr_by_li:
                first_flr_by_li[flr.line_item_id] = flr
        for act in (
            db.query(ActualsRecord)
            .filter(ActualsRecord.line_item_id.in_(li_ids))
            .order_by(ActualsRecord.period.desc())
            .all()
        ):
            if act.line_item_id not in last_actual_by_li:
                last_actual_by_li[act.line_item_id] = act

    available_items = []
    for li in line_items:
        suggestion = first_flr_by_li.get(li.id)
        prior = last_actual_by_li.get(li.id)
        available_items.append({
            "id": li.id,
            "name": li.name,
            "category": li.category,
            "account_code": li.account_code,
            "model_suggested_value": suggestion.p50 if suggestion else None,
            "confidence": suggestion.confidence_score if suggestion else None,
            "last_actual": prior.value if prior else None,
        })

    return PanelDataResponse(
        panel_type="driver_inputs",
        title=f"Driver Inputs: {version.name}",
        data={
            "version": {"id": version.id, "name": version.name, "status": version.status},
            "inputs": items,
            "form_configs": forms,
            "available_line_items": available_items,
            "total_count": len(items),
        },
    )


class DriverSubmitRequest(BaseModel):
    version_id: str
    values: dict[str, Any]  # {line_item_id: {value, source}} or field_name keyed
    notes: str | None = None
    form_config_id: str | None = None
    business_unit: str | None = None


@router.post("/driver-inputs/submit")
async def submit_driver_inputs(
    request: DriverSubmitRequest,
    current_user: User = Depends(require_permission("input")),
    db: Session = Depends(get_db),
):
    """Submit BU driver assumptions for a forecast version."""
    from app.models.driver_input import DriverFormConfig
    from app.services.driver_submission import apply_driver_submission

    version = db.query(ForecastVersion).filter(ForecastVersion.id == request.version_id).first()
    if not version:
        raise HTTPException(status_code=404, detail="Forecast version not found")
    if version.status not in ("draft", "in_review"):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot submit drivers for a '{version.status}' forecast",
        )

    bu = request.business_unit or current_user.business_unit or "Default"
    form = None
    if request.form_config_id:
        form = db.query(DriverFormConfig).filter(DriverFormConfig.id == request.form_config_id).first()
    if not form:
        form = (
            db.query(DriverFormConfig)
            .filter(DriverFormConfig.is_active == True, DriverFormConfig.business_unit == bu)
            .first()
        )
    if not form:
        # Auto-create a default form for this BU from submitted keys
        fields = []
        for key, payload in (request.values or {}).items():
            if key.startswith("_"):
                continue
            fields.append({
                "name": str(key),
                "label": str(key),
                "type": "number",
                "line_item_id": int(key) if str(key).isdigit() else None,
            })
        form = DriverFormConfig(
            business_unit=bu,
            name=f"{bu} Driver Form",
            description="Auto-generated driver form",
            fields_schema={"fields": fields or [{"name": "value", "label": "Value", "type": "number"}]},
            is_active=True,
        )
        db.add(form)
        db.flush()

    try:
        result = apply_driver_submission(
            db,
            version_id=version.id,
            form=form,
            values=request.values or {},
            user_id=current_user.id,
            business_unit=bu,
            notes=request.notes,
            apply_overrides=True,
            actor_username=current_user.username,
            audit=True,
            commit=True,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    di = result.driver_input
    return {
        "success": True,
        "id": di.id,
        "status": di.status,
        "overrides_applied": result.overrides_applied,
        "message": (
            f"Submitted {len(result.enriched_values)} driver value(s) for {bu}"
            + (f", {result.overrides_applied} applied as overrides" if result.overrides_applied else "")
        ),
    }


# ──────────────────────────────────────────────────
# Utility: Re-score existing forecasts
# ──────────────────────────────────────────────────

@router.post("/rescore-forecasts/{version_id}")
async def rescore_forecasts(
    version_id: str,
    force: bool = False,
    current_user: User = Depends(require_permission("generate")),
    db: Session = Depends(get_db),
):
    """Re-score confidence and generate remediation for an existing forecast version.

    Honors the version's pinned ``selection_rule``. When the live
    ``settings.selection_metric`` disagrees, refuse unless ``force=true``
    (audited) so FVA trends are not restamped by a rule change.
    """
    from app.services.audit import record_audit
    from app.domain.engines.model_registry import effective_selection_rule

    version = db.query(ForecastVersion).filter(ForecastVersion.id == version_id).first()
    if not version:
        raise HTTPException(status_code=404, detail="Forecast version not found")

    live_rule = effective_selection_rule()
    pinned = version.selection_rule
    if pinned and pinned != live_rule and not force:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Version selection_rule={pinned!r} differs from live rule={live_rule!r}. "
                "Pass force=true to rescore under the live confidence formula anyway "
                "(does not re-run model selection)."
            ),
        )

    results = (
        db.query(ForecastLineResult)
        .join(LineItem)
        .filter(ForecastLineResult.version_id == version_id)
        .all()
    )

    if not results:
        raise HTTPException(status_code=404, detail="No forecast results found")

    high = medium = low = 0

    for r in results:
        # Re-compute confidence score
        score = _compute_confidence_score(r)
        level = _classify_confidence(score)
        r.confidence_score = score
        r.confidence_level = level

        if level == "high":
            high += 1
        elif level == "medium":
            medium += 1
        else:
            low += 1

        # Generate remediation
        remed = _generate_remediation(r)
        r.ai_recommendation = remed["action"]
        r.ai_reasoning = remed["reason"]
        r.ai_risk_score = (
            0.0 if remed["severity"] == "info"
            else 40.0 if remed["severity"] == "warning"
            else 75.0
        )

    # Update version counts
    version.high_confidence_count = high
    version.medium_confidence_count = medium
    version.low_confidence_count = low

    record_audit(
        db,
        action="forecast.rescore",
        entity_type="forecast_version",
        entity_id=version_id,
        actor_id=current_user.id,
        actor_username=current_user.username,
        details={
            "lines": len(results),
            "high": high,
            "medium": medium,
            "low": low,
            "forced": force,
            "pinned_rule": pinned,
            "live_rule": live_rule,
        },
    )
    db.commit()

    return {
        "success": True,
        "message": f"Re-scored {len(results)} forecast lines for {version.name}",
        "high_confidence": high,
        "medium_confidence": medium,
        "low_confidence": low,
        "selection_rule": pinned,
        "forced": force,
    }


@router.post("/rescore-all")
async def rescore_all_forecasts(
    force: bool = False,
    current_user: User = Depends(require_permission("generate")),
    db: Session = Depends(get_db),
):
    """Re-score all forecast versions at once.

    Skips versions whose pinned ``selection_rule`` disagrees with the live
    rule unless ``force=true`` (audited).
    """
    from app.services.audit import record_audit
    from app.domain.engines.model_registry import effective_selection_rule

    live_rule = effective_selection_rule()
    versions = db.query(ForecastVersion).all()
    total_rescored = 0
    skipped: list[dict[str, Any]] = []
    version_ids = [v.id for v in versions]
    results_by_version: dict[str, list] = {vid: [] for vid in version_ids}
    if version_ids:
        for r in (
            db.query(ForecastLineResult)
            .join(LineItem)
            .filter(ForecastLineResult.version_id.in_(version_ids))
            .all()
        ):
            results_by_version.setdefault(r.version_id, []).append(r)

    for version in versions:
        pinned = version.selection_rule
        if pinned and pinned != live_rule and not force:
            skipped.append({"version_id": version.id, "selection_rule": pinned})
            continue
        results = results_by_version.get(version.id, [])
        high = medium = low = 0
        for r in results:
            score = _compute_confidence_score(r)
            level = _classify_confidence(score)
            r.confidence_score = score
            r.confidence_level = level
            if level == "high":
                high += 1
            elif level == "medium":
                medium += 1
            else:
                low += 1
            remed = _generate_remediation(r)
            r.ai_recommendation = remed["action"]
            r.ai_reasoning = remed["reason"]
            r.ai_risk_score = (
                0.0 if remed["severity"] == "info"
                else 40.0 if remed["severity"] == "warning"
                else 75.0
            )
        version.high_confidence_count = high
        version.medium_confidence_count = medium
        version.low_confidence_count = low
        total_rescored += len(results)

    record_audit(
        db,
        action="forecast.rescore_all",
        entity_type="forecast_version",
        entity_id=None,
        actor_id=current_user.id,
        actor_username=current_user.username,
        details={
            "versions_updated": len(versions) - len(skipped),
            "versions_skipped": len(skipped),
            "total_lines_rescored": total_rescored,
            "forced": force,
            "live_rule": live_rule,
            "skipped": skipped[:50],
        },
    )
    db.commit()

    return {
        "success": True,
        "message": (
            f"Re-scored {total_rescored} forecast lines across "
            f"{len(versions) - len(skipped)} versions"
            + (f" (skipped {len(skipped)} mismatched)" if skipped else "")
        ),
        "total_lines_rescored": total_rescored,
        "versions_skipped": len(skipped),
        "forced": force,
        "live_rule": live_rule,
    }


# ──────────────────────────────────────────────────
# Inline Override (lightweight, from forecast table)
# ──────────────────────────────────────────────────


class InlineOverrideRequest(BaseModel):
    """Quick override from the forecast table — applies to all periods of a line item."""
    result_id: str          # ForecastLineResult.id (representative row)
    new_value: float        # The user's updated value
    reason: str             # Why (min 10 chars enforced in frontend)
    apply_to: str = "all"   # "all" = all periods, "single" = only matching period


@router.post("/inline-override")
async def inline_override(
    request: InlineOverrideRequest,
    current_user: User = Depends(require_permission("override")),
    db: Session = Depends(get_db),
):
    """Apply a quick override from the forecast table.

    Creates Override records, updates ForecastLineResult, and triggers
    downstream DAG recalculation.
    """
    import uuid
    from app.services.audit import record_audit

    result = db.query(ForecastLineResult).filter(
        ForecastLineResult.id == request.result_id
    ).first()
    if not result:
        raise HTTPException(status_code=404, detail="Forecast line result not found")

    version = db.query(ForecastVersion).filter(
        ForecastVersion.id == result.version_id
    ).first()
    if not version:
        raise HTTPException(status_code=404, detail="Forecast version not found")

    # Determine which results to override
    if request.apply_to == "all":
        targets = (
            db.query(ForecastLineResult)
            .filter(
                ForecastLineResult.version_id == result.version_id,
                ForecastLineResult.line_item_id == result.line_item_id,
            )
            .all()
        )
    else:
        targets = [result]

    now = datetime.now(timezone.utc)
    overrides_created = 0

    for t in targets:
        # Check for existing active override and supersede it
        existing = (
            db.query(Override)
            .filter(
                Override.version_id == t.version_id,
                Override.line_item_id == t.line_item_id,
                Override.period == t.period,
                Override.status == "active",
            )
            .first()
        )
        if existing:
            existing.status = "superseded"

        # Create new override record
        override = Override(
            id=str(uuid.uuid4()),
            version_id=t.version_id,
            line_item_id=t.line_item_id,
            period=t.period,
            original_model_value=t.p50,
            override_value=request.new_value,
            reason=request.reason,
            status="active",
            carry_forward=True,
            user_id=current_user.id,
            created_at=now,
        )
        db.add(override)

        # Update forecast result
        t.is_overridden = True
        t.override_value = request.new_value
        overrides_created += 1

    # Recalculate downstream dependents (p10/p90) + MinT reconciliation
    downstream_count = 0
    try:
        from app.services.overrides import recalculate_and_reconcile

        downstream_count = recalculate_and_reconcile(
            db,
            result.version_id,
            [result.line_item_id],
        )
    except Exception as e:
        logger.warning(f"DAG recalculation failed (non-fatal): {e}")

    # Update version override count
    version.override_count = (
        db.query(ForecastLineResult)
        .filter(
            ForecastLineResult.version_id == result.version_id,
            ForecastLineResult.is_overridden == True,  # noqa: E712
        )
        .count()
    )

    record_audit(
        db,
        action="override.inline",
        entity_type="forecast_line_result",
        entity_id=request.result_id,
        actor_id=current_user.id,
        actor_username=current_user.username,
        details={
            "version_id": result.version_id,
            "new_value": request.new_value,
            "overrides_created": overrides_created,
            "downstream_recalculated": downstream_count,
        },
    )
    db.commit()

    li = db.query(LineItem).filter(LineItem.id == result.line_item_id).first()
    li_name = li.name if li else "Unknown"

    return {
        "success": True,
        "message": (
            f"Override applied to {li_name}: ${request.new_value:,.0f} "
            f"({overrides_created} period(s), {downstream_count} downstream recalculated)"
        ),
        "overrides_created": overrides_created,
        "downstream_recalculated": downstream_count,
        "line_item_name": li_name,
    }


# ──────────────────────────────────────────────────
# Anomaly Dashboard
# ──────────────────────────────────────────────────

@router.get("/anomaly-dashboard/{version_id}", response_model=PanelDataResponse)
async def get_anomaly_dashboard(
    version_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """AI-prioritized anomaly dashboard with business context, grouped by line item.

    Instead of dumping a flat list of statistical flags, this endpoint:
    1. Runs all detection methods but groups findings per line item
    2. Scores anomalies by materiality × statistical severity
    3. Builds a business narrative explaining each anomaly
    4. Provides specific remediation actions
    5. Returns structured data for filtering and interactive review
    """

    version = db.query(ForecastVersion).filter(ForecastVersion.id == version_id).first()
    if not version:
        raise HTTPException(status_code=404, detail="Forecast version not found")

    # ── Gather data ───────────────────────────────────
    results = (
        _scoped_results_query(db, current_user, version_id)
        .order_by(LineItem.category, LineItem.display_order, ForecastLineResult.period)
        .all()
    )

    if not results:
        return PanelDataResponse(
            panel_type="anomaly_dashboard",
            title=f"Anomaly Dashboard: {version.name}",
            data={"version": {"id": version.id, "name": version.name}, "anomalies": [], "summary": {}},
        )

    # Build actuals lookup
    actuals_map: dict[tuple[int, str], float] = {}
    all_actuals_by_li: dict[int, list[tuple[str, float]]] = {}
    if version.actuals_dataset_id:
        actuals = (
            db.query(ActualsRecord)
            .filter(ActualsRecord.dataset_id == version.actuals_dataset_id)
            .order_by(ActualsRecord.period)
            .all()
        )
        for a in actuals:
            actuals_map[(a.line_item_id, a.period)] = a.value
            all_actuals_by_li.setdefault(a.line_item_id, []).append((a.period, a.value))

    # Group forecast results by line item
    li_groups: dict[int, list[ForecastLineResult]] = {}
    for r in results:
        li_groups.setdefault(r.line_item_id, []).append(r)

    # Total for materiality
    total_p50 = sum(abs(r.p50) for r in results if r.line_item and not r.line_item.is_subtotal) or 1.0

    # ── Detect anomalies per line item ────────────────
    anomaly_items: list[dict] = []

    for li_id, group in li_groups.items():
        li = group[0].line_item
        if not li or li.is_subtotal:
            continue

        findings: list[dict] = []
        forecast_values = np.array([r.p50 for r in group])
        forecast_periods = [r.period for r in group]
        li_total_p50 = sum(abs(r.p50) for r in group)

        # Materiality
        mat_pct = li_total_p50 / total_p50 * 100 if total_p50 else 0
        materiality = "high" if mat_pct > 5 else "medium" if mat_pct > 1 else "low"
        mat_weight = 3.0 if materiality == "high" else 1.5 if materiality == "medium" else 1.0

        # Get actuals for this line item
        li_actuals = sorted(all_actuals_by_li.get(li_id, []), key=lambda x: x[0])
        actuals_values = np.array([v for _, v in li_actuals]) if li_actuals else np.array([])
        actuals_periods = [p for p, _ in li_actuals]

        # ── Method 1: Forecast vs Actuals jump ───────
        if len(actuals_values) >= 2 and len(forecast_values) >= 1:
            last_actual = actuals_values[-1]
            first_forecast = forecast_values[0]
            if abs(last_actual) > 1e-10:
                jump_pct = (first_forecast - last_actual) / abs(last_actual) * 100
                if abs(jump_pct) > 20:
                    direction = "increase" if jump_pct > 0 else "decrease"
                    dollar_change = first_forecast - last_actual
                    findings.append({
                        "type": "forecast_jump",
                        "severity": "critical" if abs(jump_pct) > 50 else "warning" if abs(jump_pct) > 30 else "info",
                        "score": abs(jump_pct) * mat_weight,
                        "headline": f"{abs(jump_pct):.0f}% {direction} vs last actuals",
                        "detail": (
                            f"The forecast for {forecast_periods[0]} is {formatCurrency(first_forecast)} "
                            f"vs the last actual of {formatCurrency(last_actual)} — "
                            f"a {formatCurrency(abs(dollar_change))} {direction}."
                        ),
                        "period": forecast_periods[0],
                        "value": first_forecast,
                        "expected": last_actual,
                    })

        # ── Method 2: Forecast internal outliers ─────
        if len(forecast_values) >= 4:
            mean_f = np.mean(forecast_values)
            std_f = np.std(forecast_values)
            if std_f > 0:
                for i, (val, period) in enumerate(zip(forecast_values, forecast_periods)):
                    z = abs(val - mean_f) / std_f
                    if z > 2.0:
                        direction = "spike" if val > mean_f else "dip"
                        findings.append({
                            "type": "forecast_outlier",
                            "severity": "critical" if z > 3.0 else "warning" if z > 2.5 else "info",
                            "score": z * mat_weight,
                            "headline": f"Forecast {direction} in {period}",
                            "detail": (
                                f"{period}: {formatCurrency(val)} vs forecast avg of {formatCurrency(mean_f)} "
                                f"(z-score: {z:.1f}). This is a {abs(val - mean_f) / abs(mean_f + 1e-10) * 100:.0f}% "
                                f"deviation from the forecast mean."
                            ),
                            "period": period,
                            "value": val,
                            "expected": mean_f,
                        })

        # ── Method 3: Actuals outliers ───────────────
        if len(actuals_values) >= 6:
            q1 = np.percentile(actuals_values, 25)
            q3 = np.percentile(actuals_values, 75)
            iqr = q3 - q1
            if iqr > 0:
                lower = q1 - 1.5 * iqr
                upper = q3 + 1.5 * iqr
                for i, (val, period) in enumerate(zip(actuals_values, actuals_periods)):
                    if val < lower or val > upper:
                        direction = "spike" if val > upper else "dip"
                        deviation = max(abs(val - lower), abs(val - upper)) / iqr
                        findings.append({
                            "type": "actuals_outlier",
                            "severity": "critical" if deviation > 2.0 else "warning" if deviation > 1.5 else "info",
                            "score": deviation * mat_weight,
                            "headline": f"Actuals {direction} in {period}",
                            "detail": (
                                f"Actual value {formatCurrency(val)} in {period} is outside the "
                                f"historical IQR range [{formatCurrency(lower)}, {formatCurrency(upper)}]. "
                                f"This could indicate a one-time event, data error, or structural change."
                            ),
                            "period": period,
                            "value": val,
                            "expected": (q1 + q3) / 2,
                        })

        # ── Method 4: Trend break detection ──────────
        if len(actuals_values) >= 8:
            x = np.arange(len(actuals_values))
            try:
                coeffs = np.polyfit(x, actuals_values, 1)
                trend = np.polyval(coeffs, x)
                monthly_growth = coeffs[0]

                # Check if forecast continues the trend
                if len(forecast_values) >= 1:
                    expected_next = trend[-1] + monthly_growth
                    actual_next = forecast_values[0]
                    if abs(expected_next) > 1e-10:
                        trend_dev = abs(actual_next - expected_next) / abs(expected_next) * 100
                        if trend_dev > 25:
                            findings.append({
                                "type": "trend_break",
                                "severity": "warning" if trend_dev > 40 else "info",
                                "score": trend_dev * mat_weight * 0.5,
                                "headline": f"Forecast deviates {trend_dev:.0f}% from historical trend",
                                "detail": (
                                    f"Based on the historical trend ({'+' if monthly_growth > 0 else ''}"
                                    f"{formatCurrency(monthly_growth)}/mo), the expected next value is "
                                    f"{formatCurrency(expected_next)}, but the forecast is {formatCurrency(actual_next)}. "
                                    f"This may be intentional (business change) or indicate a modeling issue."
                                ),
                                "period": forecast_periods[0],
                                "value": actual_next,
                                "expected": expected_next,
                            })
            except Exception:
                pass

        # ── Method 5: Wide prediction intervals ──────
        for r in group:
            if r.p10 is not None and r.p90 is not None and abs(r.p50) > 1e-10:
                band_pct = abs(r.p90 - r.p10) / abs(r.p50) * 100
                if band_pct > 80 and materiality != "low":
                    findings.append({
                        "type": "high_uncertainty",
                        "severity": "warning" if band_pct > 100 else "info",
                        "score": band_pct * mat_weight * 0.3,
                        "headline": f"Very wide confidence interval in {r.period}",
                        "detail": (
                            f"The P10-P90 range spans {formatCurrency(abs(r.p90 - r.p10))} "
                            f"({band_pct:.0f}% of the point forecast). Downside scenario is "
                            f"{formatCurrency(r.p10)}, upside is {formatCurrency(r.p90)}."
                        ),
                        "period": r.period,
                        "value": r.p50,
                        "expected": r.p50,
                    })
                    break  # Only flag once per line item

        if not findings:
            continue

        # ── Deduplicate: keep top finding per type ────
        seen_types: set[str] = set()
        unique_findings: list[dict] = []
        findings.sort(key=lambda x: -x["score"])
        for f in findings:
            key = f["type"]
            if key not in seen_types:
                seen_types.add(key)
                unique_findings.append(f)
        findings = unique_findings[:5]  # Max 5 findings per line item

        # ── Build business context ────────────────────
        top_finding = findings[0]
        severity_priority = {"critical": 0, "warning": 1, "info": 2}
        worst_severity = min(findings, key=lambda f: severity_priority.get(f["severity"], 3))["severity"]

        # Build recommended actions
        actions: list[dict[str, str]] = []
        finding_types = {f["type"] for f in findings}

        if "forecast_jump" in finding_types:
            actions.append({
                "type": "investigate",
                "label": "Validate with BU",
                "detail": f"Confirm whether a known business event explains the forecast change for {li.name}.",
            })
        if "actuals_outlier" in finding_types:
            actions.append({
                "type": "investigate",
                "label": "Check data quality",
                "detail": "Verify the actuals data — the outlier may be a data entry error or one-time event.",
            })
        if "trend_break" in finding_types or "forecast_outlier" in finding_types:
            actions.append({
                "type": "override",
                "label": "Adjust forecast",
                "detail": f"Override the forecast for {li.name} if the model projection doesn't match known business plans.",
            })
        if "high_uncertainty" in finding_types:
            actions.append({
                "type": "driver_input",
                "label": "Narrow with assumptions",
                "detail": "Collect specific driver assumptions from the BU owner to reduce forecast uncertainty.",
            })

        actions.append({
            "type": "dismiss",
            "label": "Dismiss anomaly",
            "detail": "Mark this as reviewed — the anomaly is expected or already accounted for.",
        })

        # Actuals sparkline data (last 12 months + forecast)
        sparkline = []
        for p, v in li_actuals[-12:]:
            sparkline.append({"period": p, "actual": round(v, 2)})
        for r in group[:6]:
            entry = {"period": r.period, "forecast": round(r.p50, 2)}
            actual = actuals_map.get((li_id, r.period))
            if actual is not None:
                entry["actual"] = round(actual, 2)
            sparkline.append(entry)

        anomaly_items.append({
            "id": group[0].id,
            "line_item_id": li_id,
            "line_item_name": li.name,
            "account_code": li.account_code,
            "category": li.category,
            "business_unit": li.business_unit,
            "materiality": materiality,
            "materiality_pct": round(mat_pct, 1),
            "worst_severity": worst_severity,
            "composite_score": round(top_finding["score"], 1),
            "findings": findings,
            "actions": actions,
            "sparkline": sparkline,
            "total_p50": round(li_total_p50, 2),
            "avg_p50": round(li_total_p50 / len(group), 2) if group else 0,
            "model_type": group[0].model_type,
            "is_dismissed": False,
        })

    # ── Sort by composite score (most critical first) ─
    anomaly_items.sort(key=lambda x: -x["composite_score"])

    # ── Build summary ─────────────────────────────────
    critical_items = [a for a in anomaly_items if a["worst_severity"] == "critical"]
    warning_items = [a for a in anomaly_items if a["worst_severity"] == "warning"]
    info_items = [a for a in anomaly_items if a["worst_severity"] == "info"]

    # $ at risk
    critical_value = sum(abs(a["total_p50"]) for a in critical_items)
    warning_value = sum(abs(a["total_p50"]) for a in warning_items)

    # Category breakdown
    category_counts: dict[str, dict[str, float]] = {}
    for item in anomaly_items:
        cat = str(item["category"])
        if cat not in category_counts:
            category_counts[cat] = {"critical": 0, "warning": 0, "info": 0, "total_value": 0}
        severity = str(item["worst_severity"])
        category_counts[cat][severity] = category_counts[cat].get(severity, 0) + 1
        category_counts[cat]["total_value"] += abs(float(item["total_p50"]))

    category_chart = [
        {"category": cat, **counts}
        for cat, counts in sorted(category_counts.items(), key=lambda x: -x[1]["total_value"])
    ]

    # Finding type distribution
    type_counts: dict[str, int] = {}
    for item in anomaly_items:
        for f in item["findings"]:
            ftype = str(f["type"])
            type_counts[ftype] = type_counts.get(ftype, 0) + 1

    type_labels = {
        "forecast_jump": "Forecast vs Actuals Jump",
        "forecast_outlier": "Forecast Internal Outlier",
        "actuals_outlier": "Actuals Outlier",
        "trend_break": "Trend Break",
        "high_uncertainty": "Wide Confidence Interval",
    }
    type_chart = [
        {"type": type_labels.get(t, t), "count": c}
        for t, c in sorted(type_counts.items(), key=lambda x: -x[1])
    ]

    available_categories = sorted(set(a["category"] for a in anomaly_items))

    return PanelDataResponse(
        panel_type="anomaly_dashboard",
        title=f"Anomaly Dashboard: {version.name}",
        data={
            "version": {"id": version.id, "name": version.name, "status": version.status},
            "anomalies": anomaly_items,
            "summary": {
                "total_anomalies": len(anomaly_items),
                "critical_count": len(critical_items),
                "warning_count": len(warning_items),
                "info_count": len(info_items),
                "critical_value": round(critical_value, 2),
                "warning_value": round(warning_value, 2),
                "total_line_items": len(li_groups),
            },
            "category_chart": category_chart,
            "type_chart": type_chart,
            "available_categories": available_categories,
        },
    )
