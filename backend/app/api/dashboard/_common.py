"""Shared helpers: result scoping, currency formatting and the
business-driver context builders used by several panels."""


import logging
from typing import Any

from fastapi import APIRouter
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.forecast import ForecastLineResult
from app.models.line_item import LineItem, LineItemDependency
from app.models.override import Override
from app.models.user import User
from app.services.permissions import (
    line_item_scope_filter,
)

# Scoring lives in services.confidence; remediation stays on the skill module.

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


