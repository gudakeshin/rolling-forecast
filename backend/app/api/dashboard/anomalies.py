"""Anomaly dashboard and per-user dismissals."""


import logging

import numpy as np
from fastapi import Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.auth import get_current_user
from app.database import get_db
from app.models.actuals import ActualsRecord
from app.models.anomaly_dismissal import AnomalyDismissal
from app.models.forecast import ForecastLineResult
from app.models.line_item import LineItem
from app.models.user import User
from app.schemas.forecast import PanelDataResponse
from app.services.permissions import (
    get_accessible_forecast_version,
)

# Scoring lives in services.confidence; remediation stays on the skill module.

logger = logging.getLogger(__name__)


from app.api.dashboard._common import _scoped_results_query, formatCurrency, router

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

    version = get_accessible_forecast_version(db, current_user, version_id)

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
    from app.services.vintage_anomalies import vintage_band_misses

    band_misses = vintage_band_misses(db, list(li_groups))

    dismissed_ids = {
        row.anomaly_id
        for row in db.query(AnomalyDismissal.anomaly_id).filter(
            AnomalyDismissal.user_id == current_user.id,
            AnomalyDismissal.version_id == version_id,
        )
    }

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

        # ── Method 6: Actual outside a prior vintage's band (Phase E5) ──
        sev_score = {"critical": 3.0, "warning": 2.0, "info": 1.0}
        for miss in band_misses.get(li_id, []):
            side = "above" if miss["direction"] == "above" else "below"
            widths = miss["band_widths_outside"]
            findings.append({
                "type": "vintage_band_miss",
                "severity": miss["severity"],
                "score": (widths if widths is not None else 3.0) * 30 * mat_weight
                + sev_score[miss["severity"]],
                "headline": f"Actual landed {side} the forecast range in {miss['period']}",
                "detail": (
                    f"The {miss['period']} actual of {formatCurrency(miss['value'])} is {side} the "
                    f"P10-P90 of {formatCurrency(miss['p10'])} to {formatCurrency(miss['p90'])} that the "
                    f"forecast made {miss['horizon_offset']} month(s) earlier published"
                    + (f" ({widths:.1f} band widths outside)." if widths is not None else ".")
                    + " Either something unusual happened, or the band was too narrow."
                ),
                "period": miss["period"],
                "value": miss["value"],
                "expected": miss["expected"],
                "vintage_version_id": miss["vintage_version_id"],
            })

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
        if "vintage_band_miss" in finding_types:
            actions.append({
                "type": "investigate",
                "label": "Explain the miss",
                "detail": (
                    f"Find out what drove {li.name} outside its forecast range — a one-off, a "
                    "data issue, or a change the forecast should now carry."
                ),
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
            "is_dismissed": group[0].id in dismissed_ids,
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


class AnomalyDismissRequest(BaseModel):
    anomaly_id: str


@router.post("/anomaly-dashboard/{version_id}/dismiss")
async def dismiss_anomaly(
    version_id: str,
    body: AnomalyDismissRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Mark an anomaly finding reviewed so it stays dismissed across reopens.

    Per-user, non-destructive (only hides the item from this user's own
    view) — same access level as viewing the dashboard itself, not the
    elevated "review" permission a workflow decision would need.
    """
    existing = (
        db.query(AnomalyDismissal)
        .filter(
            AnomalyDismissal.user_id == current_user.id,
            AnomalyDismissal.anomaly_id == body.anomaly_id,
        )
        .first()
    )
    if existing:
        return {"success": True, "anomaly_id": body.anomaly_id, "already_dismissed": True}
    db.add(AnomalyDismissal(
        user_id=current_user.id,
        version_id=version_id,
        anomaly_id=body.anomaly_id,
    ))
    db.commit()
    return {"success": True, "anomaly_id": body.anomaly_id, "already_dismissed": False}


@router.post("/anomaly-dashboard/{version_id}/undismiss")
async def undismiss_anomaly(
    version_id: str,
    body: AnomalyDismissRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Reverse a dismissal (the "show dismissed" restore action)."""
    _ = version_id  # kept for route symmetry with dismiss; identity is (user, anomaly_id)
    deleted = (
        db.query(AnomalyDismissal)
        .filter(
            AnomalyDismissal.user_id == current_user.id,
            AnomalyDismissal.anomaly_id == body.anomaly_id,
        )
        .delete()
    )
    db.commit()
    return {"success": True, "anomaly_id": body.anomaly_id, "was_dismissed": bool(deleted)}


