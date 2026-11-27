"""Review dashboard (AI triage) and the review actions: per-item,
batch, accept-AI and undo."""


import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.auth import get_current_user
from app.database import get_db
from app.models.actuals import ActualsRecord
from app.models.forecast import ForecastLineResult, ForecastVersion, ModelMetadata
from app.models.review_undo import ReviewUndoSnapshot
from app.models.user import User
from app.schemas.forecast import PanelDataResponse
from app.services.permissions import (
    get_accessible_forecast_version,
    require_permission,
)

# Scoring lives in services.confidence; remediation stays on the skill module.

logger = logging.getLogger(__name__)


from app.api.dashboard._common import (
    _build_driver_context,
    _extract_exog_payload,
    _preload_driver_context,
    _scoped_results_query,
    router,
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
    version = get_accessible_forecast_version(db, current_user, version_id)

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

# How long a batch/single review action stays reversible. Comfortably longer
# than the frontend's undo toast so a slow click never races the expiry.
REVIEW_UNDO_TTL_SECONDS = 30


def _snapshot_review_state(rows: list[ForecastLineResult]) -> list[dict[str, Any]]:
    return [
        {
            "id": r.id,
            "review_status": r.review_status,
            "review_comment": r.review_comment,
            "reviewed_by": r.reviewed_by,
            "reviewed_at": r.reviewed_at.isoformat() if r.reviewed_at else None,
        }
        for r in rows
    ]


def _create_undo_snapshot(
    db: Session,
    *,
    version_id: str,
    actor_id: str,
    action: str,
    description: str,
    prior_state: list[dict[str, Any]],
) -> str:
    """Persist an already-captured pre-mutation ``prior_state`` and return an undo token.

    ``prior_state`` must come from ``_snapshot_review_state`` called BEFORE the
    caller mutates its rows — the ORM objects are mutated in place, so
    snapshotting them after the fact would just record the new values.
    """
    snapshot = ReviewUndoSnapshot(
        version_id=version_id,
        actor_id=actor_id,
        action=action,
        description=description,
        prior_state=prior_state,
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=REVIEW_UNDO_TTL_SECONDS),
    )
    db.add(snapshot)
    db.flush()
    return snapshot.id


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


class UndoReviewRequest(BaseModel):
    """Reverse a previous review_item / batch_review / accept_ai_recommendations call."""
    undo_token: str


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

    undo_token = _create_undo_snapshot(
        db,
        version_id=result.version_id,
        actor_id=current_user.id,
        action="review_item",
        description=f"{request.action.title()}d line item",
        prior_state=_snapshot_review_state(all_periods),
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
        "undo_token": undo_token,
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
    prior_state: list[dict[str, Any]] = []

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
        # Snapshot before mutating — these ORM objects are overwritten in place.
        prior_state.extend(_snapshot_review_state(all_periods))

        for r in all_periods:
            r.review_status = request.action
            r.review_comment = request.comment
            r.reviewed_by = current_user.id
            r.reviewed_at = now

        total_affected += len(all_periods)

    undo_token = (
        _create_undo_snapshot(
            db,
            version_id=request.version_id,
            actor_id=current_user.id,
            action="batch_review",
            description=f"{request.action.title()}d {len(request.item_ids)} line item(s)",
            prior_state=prior_state,
        )
        if prior_state
        else None
    )

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
        "undo_token": undo_token,
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

    undo_token = (
        _create_undo_snapshot(
            db,
            version_id=request.version_id,
            actor_id=current_user.id,
            action="accept_ai_recommendations",
            description=f"Auto-approved {len(ai_approved)} line-period(s)",
            prior_state=_snapshot_review_state(ai_approved),
        )
        if ai_approved
        else None
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
        "undo_token": undo_token,
    }


@router.post("/undo-review")
async def undo_review(
    request: UndoReviewRequest,
    current_user: User = Depends(require_permission("review")),
    db: Session = Depends(get_db),
):
    """Reverse a review_item / batch_review / accept_ai_recommendations call.

    Restores every touched ForecastLineResult to its exact pre-action state
    (including back to "never reviewed" when that was the prior state) within
    the token's short window. One-shot: a consumed or expired token 404s/409s
    rather than silently no-op-ing, so the frontend can tell the analyst why
    the undo button stopped working.
    """
    from app.services.audit import record_audit

    snapshot = db.query(ReviewUndoSnapshot).filter(
        ReviewUndoSnapshot.id == request.undo_token
    ).first()
    if not snapshot:
        raise HTTPException(
            status_code=404, detail="Nothing to undo — this action can no longer be reversed"
        )
    if snapshot.actor_id != current_user.id:
        raise HTTPException(status_code=403, detail="You can only undo your own actions")
    if snapshot.consumed_at is not None:
        raise HTTPException(status_code=409, detail="This action has already been undone")

    expires_at = snapshot.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=410, detail="The undo window for this action has expired")

    prior_by_id = {row["id"]: row for row in snapshot.prior_state}
    touched = (
        db.query(ForecastLineResult)
        .filter(ForecastLineResult.id.in_(list(prior_by_id.keys())))
        .all()
    )
    restored = 0
    for r in touched:
        prior = prior_by_id.get(r.id)
        if prior is None:
            continue
        r.review_status = prior["review_status"]
        r.review_comment = prior["review_comment"]
        r.reviewed_by = prior["reviewed_by"]
        r.reviewed_at = (
            datetime.fromisoformat(prior["reviewed_at"]) if prior["reviewed_at"] else None
        )
        restored += 1

    snapshot.consumed_at = datetime.now(timezone.utc)

    record_audit(
        db,
        action="review.undo",
        entity_type="forecast_version",
        entity_id=snapshot.version_id,
        actor_id=current_user.id,
        actor_username=current_user.username,
        details={
            "undo_token": request.undo_token,
            "original_action": snapshot.action,
            "restored_count": restored,
        },
    )
    db.commit()

    return {
        "success": True,
        "message": f"Undone: {snapshot.description}",
        "restored_count": restored,
    }


