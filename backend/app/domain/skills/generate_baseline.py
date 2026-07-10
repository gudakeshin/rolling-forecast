"""GenerateBaseline skill -- runs statistical models to produce forecast baseline.

Handles PRD Section 10 edge cases:
- EC1: Line item has < 12 months of history (use simpler model, low confidence)
- EC2: Line item has all zeros (skip modeling, flag for manual input)
- EC4: Actuals missing for most recent period (warn about stale baseline)
- EC8: Model selection ties (prefer simpler model)
- EC9: Negative values for non-negative line (clamp to zero, flag)
- EC11: Generation timeout (terminate and alert)
- EC12: Structural break detected (flag for analyst)

Includes automatic inline confidence scoring and remediation recommendations
so that every generated forecast immediately has quality scores and actionable next steps.
"""

import time
import logging
from typing import Any
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from app.domain.base_skill import BaseSkill, SkillContext, SkillResult
from app.domain.engines.model_registry import get_model_registry
from app.models.actuals import ActualsDataset, ActualsRecord
from app.models.line_item import LineItem
from app.models.forecast import ForecastVersion, ForecastLineResult, ModelMetadata
from app.config import settings
from app.services.error_handlers import (
    HistoryAnalysis,
    clamp_forecast_values,
    check_generation_timeout,
    ForecastTimeoutError,
)

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────
# Inline confidence scoring & remediation logic
# ──────────────────────────────────────────────────────

# Model type base confidence scores (simpler models = lower base)
_MODEL_BASE_SCORES = {"prophet": 65, "arima": 60, "ets": 55, "linear": 40, "average": 25, "zero": 0}


def _compute_confidence_score(result: ForecastLineResult) -> float:
    """Compute composite confidence score (0-100) for a single forecast line.

    # Blends CV MAPE (not in-sample fit residual) when available:
    # - Model CV MAPE (40%): lower MAPE = higher confidence
    - Prediction interval width (25%): narrower = more confident
    - R-squared goodness of fit (20%): higher = better
    - Model type base score (15%): sophisticated models get higher base
    """
    scores: list[float] = []
    weights: list[float] = []

    # 1. MAPE (40%)
    if result.model_mape is not None and result.model_mape > 0:
        mape_score = max(0, 100 - result.model_mape * 5)  # 20% MAPE -> score 0
        scores.append(mape_score)
        weights.append(0.40)

    # 2. Prediction interval width (25%)
    if result.p10 is not None and result.p90 is not None and result.p50 != 0:
        interval_width = abs(result.p90 - result.p10)
        relative_width = interval_width / (abs(result.p50) + 1e-10)
        width_score = max(0, 100 - relative_width * 100)
        scores.append(width_score)
        weights.append(0.25)

    # 3. R-squared (20%)
    if result.model_r_squared is not None:
        r2_score = max(0, result.model_r_squared * 100)
        scores.append(r2_score)
        weights.append(0.20)

    # 4. Model type base score (15%)
    base = _MODEL_BASE_SCORES.get(result.model_type, 50)
    scores.append(base)
    weights.append(0.15)

    if not scores:
        return 50.0

    total_weight = sum(weights)
    weighted_score = sum(s * w for s, w in zip(scores, weights)) / total_weight
    return round(max(0, min(100, weighted_score)), 1)


def _classify_confidence(score: float, threshold_low: int = 50, threshold_medium: int = 70) -> str:
    """Classify confidence score into high / medium / low."""
    if score >= threshold_medium:
        return "high"
    elif score >= threshold_low:
        return "medium"
    return "low"


def _generate_remediation(
    result: ForecastLineResult,
    analysis_flags: list[str] | None = None,
) -> dict[str, Any]:
    """Generate specific, actionable remediation recommendation for a forecast line.

    Returns:
        {
            "action": "approve" | "review" | "override" | "manual_input",
            "severity": "info" | "warning" | "critical",
            "reason": str,
            "suggestion": str,
        }
    """
    issues: list[str] = []
    severity = "info"

    # Zero-activity line → needs manual input
    if result.model_type == "zero":
        return {
            "action": "manual_input",
            "severity": "critical",
            "reason": "Line item has zero historical activity",
            "suggestion": "Provide a manual forecast or confirm this line will remain zero. "
                         "Use the override tool to set expected values.",
        }

    # Average model (sparse data) → review & enrich
    if result.model_type == "average":
        return {
            "action": "review",
            "severity": "warning",
            "reason": "Insufficient history (< 12 months) — using simple average",
            "suggestion": "Upload additional historical data to enable statistical modeling. "
                         "Consider applying a manual override based on business knowledge.",
        }

    # High MAPE → model fit is poor
    if result.model_mape is not None and result.model_mape > 20:
        issues.append(f"Model error is high (MAPE: {result.model_mape:.1f}%)")
        severity = "critical"
    elif result.model_mape is not None and result.model_mape > 12:
        issues.append(f"Moderate model error (MAPE: {result.model_mape:.1f}%)")
        severity = "warning"

    # Wide prediction interval → high uncertainty
    if result.p10 is not None and result.p90 is not None and result.p50 != 0:
        band_pct = abs(result.p90 - result.p10) / (abs(result.p50) + 1e-10) * 100
        if band_pct > 80:
            issues.append(f"Very wide uncertainty band ({band_pct:.0f}% of forecast)")
            severity = "critical"
        elif band_pct > 50:
            issues.append(f"Wide uncertainty band ({band_pct:.0f}% of forecast)")
            if severity != "critical":
                severity = "warning"

    # Low R-squared → poor fit
    if result.model_r_squared is not None and result.model_r_squared < 0.3:
        issues.append(f"Poor model fit (R²: {result.model_r_squared:.2f})")
        if severity != "critical":
            severity = "warning"

    # Include analysis flags (structural break, etc.)
    if analysis_flags:
        for flag in analysis_flags:
            issues.append(flag)
            severity = "warning"

    if not issues:
        return {
            "action": "approve",
            "severity": "info",
            "reason": "All quality checks passed",
            "suggestion": "Forecast looks reliable — can be auto-approved.",
        }

    # Build suggestion based on issues
    suggestions = []
    if any("MAPE" in i or "model error" in i.lower() for i in issues):
        suggestions.append("Consider trying a different model or applying an override based on business insight.")
    if any("band" in i.lower() or "uncertainty" in i.lower() for i in issues):
        suggestions.append("Narrow the forecast with driver inputs or manual adjustment.")
    if any("R²" in i or "fit" in i.lower() for i in issues):
        suggestions.append("Review underlying data for anomalies or structural changes.")
    if any("break" in i.lower() for i in issues):
        suggestions.append("A structural break was detected — validate if business conditions changed.")

    action = "override" if severity == "critical" else "review"

    return {
        "action": action,
        "severity": severity,
        "reason": "; ".join(issues),
        "suggestion": " ".join(suggestions) if suggestions else "Review this item and consider applying a manual override.",
    }


class GenerateBaselineSkill(BaseSkill):
    """Skill to generate a statistical baseline forecast for all P&L line items."""

    @property
    def name(self) -> str:
        return "generate_baseline"

    @property
    def description(self) -> str:
        return (
            "Generate a statistical baseline forecast for all P&L line items using "
            "the best-fit model (ARIMA, Prophet, ETS, or linear trend). Automatically "
            "selects the best model per line item using walk-forward cross-validation. "
            "Produces point forecasts with confidence intervals (P10/P50/P90). "
            "Handles sparse data, all-zero lines, structural breaks, and timeouts. "
            "Use this when the user wants to generate or refresh a forecast."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "dataset_id": {
                    "type": "string",
                    "description": "ID of the actuals dataset to use (uses latest if not specified)",
                },
                "horizon_months": {
                    "type": "integer",
                    "description": "Number of months to forecast forward (default: 12)",
                    "default": 12,
                },
                "model_type": {
                    "type": "string",
                    "description": "Force a single model type for ALL line items. Default 'auto' runs walk-forward cross-validation to pick the BEST model per line item (ARIMA, ETS, Prophet, or Linear). Only use a specific model if the user explicitly requests it.",
                    "enum": ["auto", "arima", "prophet", "ets", "linear"],
                    "default": "auto",
                },
                "models_to_test": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["arima", "prophet", "ets", "linear"]},
                    "description": "Subset of models to test during auto-selection. If not specified, all 4 models are tested. Use this when the user wants to limit which algorithms are compared.",
                },
                "random_seed": {
                    "type": "integer",
                    "description": "Random seed for reproducibility (default: 42)",
                    "default": 42,
                },
                "async_job": {
                    "type": "boolean",
                    "description": "If true and REDIS_URL is set, enqueue generation on the arq worker and return a job_id",
                    "default": False,
                },
            },
        }

    @property
    def required_role(self) -> str:
        return "generate"

    async def execute(self, params: dict[str, Any], context: SkillContext) -> SkillResult:
        """Generate baseline forecast for all line items with full edge case handling."""
        db: Session = context.db
        start_time = time.time()

        # Optional async enqueue when Redis/arq is available
        if params.get("async_job"):
            from app.services.job_queue import enqueue_generate_baseline, redis_configured

            if redis_configured():
                job = await enqueue_generate_baseline(
                    version_name="pending",
                    params={k: v for k, v in params.items() if k != "async_job"},
                    user_id=context.user_id,
                    conversation_id=context.conversation_id,
                )
                return SkillResult.ok(
                    message=f"Baseline generation queued (job {job['job_id']})",
                    data=job,
                    content_blocks=[
                        self._status_block(
                            label="Queued forecast generation",
                            progress=0.05,
                            step=f"job_id={job['job_id']}",
                        ),
                        self._text_block(
                            f"Job `{job['job_id']}` queued. Poll `GET /api/jobs/{job['job_id']}` for status."
                        ),
                    ],
                )
            # No Redis — fall through to synchronous execution
            params = {**params, "async_job": False}

        horizon = params.get("horizon_months", settings.default_horizon_months)
        model_type = params.get("model_type", "auto")
        models_to_test = params.get("models_to_test", None)  # None = all models
        random_seed = params.get("random_seed", 42)

        # Soft gate: prefer plan_forecast before baseline (warn, do not hard-block)
        plan_done = context.context_manager.get_memory("last_plan_id") or context.context_manager.get_memory("plan_forecast_complete")
        plan_warning = None
        if not plan_done and not params.get("skip_plan_check"):
            plan_warning = (
                "Note: plan_forecast was not run in this session. "
                "Proceeding with baseline generation; run plan_forecast first for model selection guidance."
            )

        # Get dataset
        dataset_id = params.get("dataset_id") or context.context_manager.get_memory("last_dataset_id")
        if not dataset_id:
            dataset = db.query(ActualsDataset).order_by(ActualsDataset.ingested_at.desc()).first()
            if not dataset:
                return SkillResult.fail("No actuals data found. Please upload actuals first.")
            dataset_id = dataset.id
        else:
            dataset = db.query(ActualsDataset).filter(ActualsDataset.id == dataset_id).first()
            if not dataset:
                return SkillResult.fail(f"Dataset '{dataset_id}' not found.")

        # Get non-calculated line items in the caller's BU scope
        from app.services.permissions import resolve_skill_user, scoped_line_items

        actor = resolve_skill_user(context)
        line_items = scoped_line_items(
            db, actor, LineItem.is_calculated == False  # noqa: E712
        ).all()
        if not line_items:
            return SkillResult.fail("No line items found. Please ingest actuals data first.")

        # Create forecast version
        existing_count = db.query(ForecastVersion).count()
        now = datetime.now(timezone.utc)
        version_name = f"FC-{now.strftime('%Y-%m')}-v{existing_count + 1}"

        version = ForecastVersion(
            name=version_name,
            status="draft",
            version_type="scheduled",
            actuals_dataset_id=dataset_id,
            actuals_hash=dataset.file_hash,
            horizon_months=horizon,
            base_period=dataset.period_end,
            random_seed=random_seed,
            created_by=context.user_id,  # SoD: creator cannot self-approve
        )
        # FX: resolve reporting currency up front
        from app.services.fx import get_reporting_currency, MissingFxRateError, convert_series_values
        reporting_ccy = get_reporting_currency(db)
        version.reporting_currency = reporting_ccy
        db.add(version)
        db.flush()
        fx_hashes: list[str] = []

        # Generate forecast for each line item
        model_registry = get_model_registry()
        summary: dict[str, Any] = {
            "total": 0,
            "success": 0,
            "skipped": 0,
            "zero_lines": 0,
            "sparse_lines": 0,
            "structural_breaks": 0,
            "negative_clamped": 0,
            "model_distribution": {},
            "model_comparisons": {},  # line_item_name -> comparison results
        }
        all_warnings: list[str] = []
        all_flags: list[dict] = []

        try:
            for li in line_items:
                summary["total"] += 1

                # EC11: Check timeout periodically
                try:
                    check_generation_timeout(
                        start_time,
                        context=f"Processing line {summary['total']}/{len(line_items)}: {li.name}",
                    )
                except ForecastTimeoutError as e:
                    # Save partial results
                    version.generation_time_seconds = time.time() - start_time
                    db.commit()
                    all_warnings.append(str(e))
                    return self._build_timeout_response(version, summary, all_warnings, all_flags)

                # Get actuals for this line item
                records = (
                    db.query(ActualsRecord)
                    .filter(
                        ActualsRecord.dataset_id == dataset_id,
                        ActualsRecord.line_item_id == li.id,
                    )
                    .order_by(ActualsRecord.period)
                    .all()
                )

                if not records:
                    summary["skipped"] += 1
                    continue

                # Build time series — convert to reporting currency at construction time
                raw_values = [float(r.value) for r in records]
                periods = [r.period for r in records]
                currencies = [getattr(r, "currency", None) or reporting_ccy for r in records]
                try:
                    converted, fx_hash = convert_series_values(
                        db, raw_values, currencies, periods, reporting_ccy
                    )
                    fx_hashes.append(fx_hash)
                except MissingFxRateError as e:
                    return SkillResult.fail(str(e))
                values = pd.Series(converted)
                dates = pd.DatetimeIndex([pd.Timestamp(p + "-01") for p in periods])

                # Run history analysis (EC1, EC2, EC4, EC12)
                analysis = HistoryAnalysis(values, dates, li.name)
                analysis_result = analysis.analyze()

                for w in analysis_result["warnings"]:
                    all_warnings.append(w)

                if analysis_result["flags"]:
                    all_flags.append({
                        "line_item": li.name,
                        "flags": analysis_result["flags"],
                    })

                # EC2: All zeros -- skip modeling
                if analysis.is_all_zeros:
                    summary["zero_lines"] += 1
                    for i in range(horizon):
                        current = dates[-1] + pd.offsets.MonthBegin(i + 1)
                        line_result = ForecastLineResult(
                            version_id=version.id,
                            line_item_id=li.id,
                            period=current.strftime("%Y-%m"),
                            p10=0.0, p50=0.0, p90=0.0,
                            confidence_score=0,
                            confidence_level="low",
                            model_type="zero",
                        )
                        db.add(line_result)
                    summary["success"] += 1
                    continue

                # EC1: Very sparse data -- use simple average
                if analysis.is_very_sparse:
                    summary["sparse_lines"] += 1
                    avg_val = float(values.mean())
                    std_val = float(values.std()) if len(values) > 1 else avg_val * 0.2
                    for i in range(horizon):
                        current = dates[-1] + pd.offsets.MonthBegin(i + 1)
                        p50 = max(0, avg_val) if not li.allow_negative else avg_val
                        line_result = ForecastLineResult(
                            version_id=version.id,
                            line_item_id=li.id,
                            period=current.strftime("%Y-%m"),
                            p10=max(0, p50 - 1.28 * std_val) if not li.allow_negative else p50 - 1.28 * std_val,
                            p50=p50,
                            p90=p50 + 1.28 * std_val,
                            confidence_score=0,
                            confidence_level="low",
                            model_type="average",
                        )
                        db.add(line_result)
                    summary["success"] += 1
                    continue

                # EC12: Structural break -- use post-break data only
                effective_values = values
                effective_dates = dates
                if analysis.has_structural_break and analysis.structural_break_period:
                    summary["structural_breaks"] += 1
                    break_idx = None
                    for idx, d in enumerate(dates):
                        if d.strftime("%Y-%m") == analysis.structural_break_period:
                            break_idx = idx
                            break
                    if break_idx is not None and (len(values) - break_idx) >= 6:
                        effective_values = values[break_idx:]
                        effective_dates = dates[break_idx:]

                # Select and run model
                try:
                    # EC1: Force simpler model for sparse data
                    effective_model_type = model_type
                    if analysis.is_sparse and model_type == "auto":
                        effective_model_type = "linear"

                    if effective_model_type == "auto":
                        selected_model, selection_mape, comparison = model_registry.auto_select(
                            effective_values, effective_dates,
                            random_seed=random_seed,
                            models_to_test=models_to_test,
                        )
                        # Store comparison results per line item
                        summary["model_comparisons"][li.name] = comparison.to_dict()
                    else:
                        selected_model = effective_model_type
                        selection_mape = None

                    forecast_output = model_registry.fit_and_predict(
                        selected_model, effective_values, effective_dates, horizon,
                        random_seed=random_seed,
                    )

                    # Prefer CV MAPE for confidence; fall back to in-sample only if no CV
                    cv_mape = selection_mape if selection_mape is not None else None
                    in_sample = forecast_output.fit_metrics.get("in_sample_mape") or forecast_output.fit_metrics.get("mape")
                    honest_mape = cv_mape if cv_mape is not None and cv_mape != float("inf") else in_sample

                    # EC9: Clamp negative values
                    point_forecast = forecast_output.point_forecast.copy()
                    lower_bound = forecast_output.lower_bound.copy() if forecast_output.lower_bound is not None else None

                    point_forecast, clamp_warnings = clamp_forecast_values(
                        point_forecast, li.allow_negative, li.name
                    )
                    if clamp_warnings:
                        summary["negative_clamped"] += 1
                        all_warnings.extend(clamp_warnings)

                    if lower_bound is not None and not li.allow_negative:
                        lower_bound = np.maximum(lower_bound, 0)

                    # Track model distribution
                    summary["model_distribution"][selected_model] = (
                        summary["model_distribution"].get(selected_model, 0) + 1
                    )

                    # Store results for each forecast period
                    for i, period in enumerate(forecast_output.periods):
                        p50 = float(point_forecast[i])
                        p10 = float(lower_bound[i]) if lower_bound is not None else None
                        p90 = float(forecast_output.upper_bound[i]) if forecast_output.upper_bound is not None else None

                        line_result = ForecastLineResult(
                            version_id=version.id,
                            line_item_id=li.id,
                            period=period,
                            p10=p10, p50=p50, p90=p90,
                            confidence_score=0,
                            confidence_level="pending",
                            model_type=selected_model,
                            model_mape=honest_mape,
                            model_r_squared=forecast_output.fit_metrics.get("r_squared"),
                        )
                        db.add(line_result)
                        db.flush()  # Ensure line_result.id is assigned

                        # Store model metadata (first period only)
                        if i == 0:
                            metadata = ModelMetadata(
                                line_result_id=line_result.id,
                                model_type=selected_model,
                                parameters=forecast_output.parameters,
                                training_window_start=periods[0],
                                training_window_end=periods[-1],
                                training_points=len(records),
                                mape=honest_mape,
                                r_squared=forecast_output.fit_metrics.get("r_squared"),
                                aic=forecast_output.fit_metrics.get("aic"),
                                seasonality_detected=forecast_output.diagnostics.get("seasonality_detected", False),
                                seasonality_period=forecast_output.diagnostics.get("seasonality_period"),
                                structural_break_detected=analysis.has_structural_break,
                                structural_break_period=analysis.structural_break_period,
                                random_seed=random_seed,
                            )
                            db.add(metadata)

                    summary["success"] += 1

                except Exception as e:
                    logger.error(f"Forecast failed for {li.account_code}: {e}", exc_info=True)
                    summary["skipped"] += 1
                    all_warnings.append(f"Model failed for '{li.name}': {str(e)[:100]}")

        except Exception as e:
            logger.error(f"Forecast generation error: {e}", exc_info=True)
            all_warnings.append(f"Generation error: {str(e)[:200]}")

        # ──────────────────────────────────────────────────
        # PHASE: Inline confidence scoring & remediation
        # ──────────────────────────────────────────────────
        logger.info("Starting inline confidence scoring for %d line results...", summary["success"])

        line_results = (
            db.query(ForecastLineResult)
            .filter(ForecastLineResult.version_id == version.id)
            .all()
        )

        high_count = 0
        medium_count = 0
        low_count = 0
        remediation_items: list[dict] = []

        # Build a flag lookup: line_item_name -> flags list
        flag_lookup: dict[str, list[str]] = {}
        for fl in all_flags:
            flag_lookup[fl["line_item"]] = fl.get("flags", [])

        for result in line_results:
            # Score confidence
            score = _compute_confidence_score(result)
            level = _classify_confidence(score)
            result.confidence_score = score
            result.confidence_level = level

            if level == "high":
                high_count += 1
            elif level == "medium":
                medium_count += 1
            else:
                low_count += 1

            # Generate remediation for each line
            li_name = result.line_item.name if result.line_item else "Unknown"
            li_flags = flag_lookup.get(li_name)
            remed = _generate_remediation(result, analysis_flags=li_flags)

            # Persist AI analysis fields
            result.ai_recommendation = remed["action"]
            result.ai_reasoning = remed["reason"]
            result.ai_risk_score = (
                0.0 if remed["severity"] == "info"
                else 40.0 if remed["severity"] == "warning"
                else 75.0
            )

            # Collect non-trivial remediation items (dedup per line item later)
            if remed["severity"] != "info":
                remediation_items.append({
                    "line_item": li_name,
                    "category": result.line_item.category if result.line_item else "Unknown",
                    "period": result.period,
                    "score": score,
                    "level": level,
                    "model": result.model_type,
                    "severity": remed["severity"],
                    "action": remed["action"],
                    "reason": remed["reason"],
                    "suggestion": remed["suggestion"],
                })

        # Update version summary with confidence counts
        version.high_confidence_count = high_count
        version.medium_confidence_count = medium_count
        version.low_confidence_count = low_count

        # Update version summary
        elapsed = time.time() - start_time
        version.total_line_items = summary["success"]
        version.generation_time_seconds = elapsed
        if fx_hashes:
            import hashlib
            version.fx_rate_set_hash = hashlib.sha256(
                "|".join(sorted(set(fx_hashes))).encode()
            ).hexdigest()
        version.model_versions = {
            "models": model_registry.list_models(),
            "selection_method": "rolling_origin_cv" if model_type == "auto" else "manual",
        }

        db.commit()
        logger.info(
            "Confidence scoring complete: %d high, %d medium, %d low",
            high_count, medium_count, low_count,
        )

        # Set active version
        context.context_manager.set_active_version_id(version.id)

        # Dedup remediation items by line item (keep worst per line)
        seen_lines: set[str] = set()
        unique_remediation: list[dict] = []
        for item in sorted(remediation_items, key=lambda x: x["score"]):
            if item["line_item"] not in seen_lines:
                seen_lines.add(item["line_item"])
                unique_remediation.append(item)

        return self._build_success_response(
            version, horizon, dataset, model_type, summary, elapsed,
            all_warnings, all_flags, high_count, medium_count, low_count,
            unique_remediation,
            plan_warning=plan_warning,
        )

    def _build_success_response(
        self, version, horizon, dataset, model_type, summary, elapsed,
        warnings, flags, high_count, medium_count, low_count, remediation_items,
        plan_warning=None,
    ) -> SkillResult:
        """Build the rich response with confidence scores, remediation, and actions."""
        total_scored = high_count + medium_count + low_count

        content_blocks = []
        if plan_warning:
            content_blocks.append(self._text_block(f"**Planning note:** {plan_warning}"))

        content_blocks.extend([
            self._text_block(
                f"Generated baseline forecast **{version.name}** with a {horizon}-month horizon. "
                f"Confidence scoring complete."
            ),
            self._table_block(
                title="Generation Summary",
                columns=[
                    {"key": "metric", "label": "Metric"},
                    {"key": "value", "label": "Value"},
                ],
                rows=[
                    {"metric": "Version", "value": version.name},
                    {"metric": "Lines forecasted", "value": str(summary["success"])},
                    {"metric": "Lines skipped", "value": str(summary["skipped"])},
                    {"metric": "Zero-activity lines", "value": str(summary["zero_lines"])},
                    {"metric": "Sparse data (< 12 months)", "value": str(summary["sparse_lines"])},
                    {"metric": "Structural breaks", "value": str(summary["structural_breaks"])},
                    {"metric": "Negative values clamped", "value": str(summary["negative_clamped"])},
                    {"metric": "Horizon", "value": f"{horizon} months"},
                    {"metric": "Base period", "value": dataset.period_end},
                    {"metric": "Generation time", "value": f"{elapsed:.1f}s"},
                    {"metric": "Model selection", "value": "Auto (walk-forward CV)" if model_type == "auto" else model_type},
                ],
            ),
        ])

        # Confidence distribution table
        content_blocks.append(
            self._table_block(
                title="Confidence Distribution",
                columns=[
                    {"key": "level", "label": "Confidence Level"},
                    {"key": "count", "label": "Count"},
                    {"key": "pct", "label": "%"},
                    {"key": "action", "label": "Recommended Action"},
                ],
                rows=[
                    {
                        "level": "High (70+)",
                        "count": str(high_count),
                        "pct": f"{high_count/total_scored*100:.0f}%" if total_scored > 0 else "0%",
                        "action": "Auto-approvable",
                    },
                    {
                        "level": "Medium (50-70)",
                        "count": str(medium_count),
                        "pct": f"{medium_count/total_scored*100:.0f}%" if total_scored > 0 else "0%",
                        "action": "Review recommended",
                    },
                    {
                        "level": "Low (<50)",
                        "count": str(low_count),
                        "pct": f"{low_count/total_scored*100:.0f}%" if total_scored > 0 else "0%",
                        "action": "Review required / Override",
                    },
                ],
            ),
        )

        if summary["model_distribution"]:
            dist_rows = [
                {"model": model, "count": str(count)}
                for model, count in sorted(
                    summary["model_distribution"].items(),
                    key=lambda x: x[1], reverse=True,
                )
            ]
            content_blocks.append(
                self._table_block(
                    title="Model Distribution (Best Model Per Line Item)",
                    columns=[
                        {"key": "model", "label": "Model"},
                        {"key": "count", "label": "Lines"},
                    ],
                    rows=dist_rows,
                )
            )

        # Model comparison MAPE table — shows how each algorithm performed
        if summary.get("model_comparisons"):
            comparison_rows = []
            for li_name, comp_data in sorted(summary["model_comparisons"].items()):
                row = {"line_item": li_name, "selected": comp_data["best_model"]}
                for c in comp_data.get("comparisons", []):
                    mape_val = c.get("mape")
                    if mape_val is not None:
                        label = f"{mape_val:.1f}%"
                        if c.get("selected"):
                            label += " ★"
                    elif c.get("error"):
                        label = "N/A"
                    else:
                        label = "-"
                    row[c["model"]] = label
                comparison_rows.append(row)

            if comparison_rows:
                # Determine which model columns exist
                all_model_cols = set()
                for r in comparison_rows:
                    for k in r:
                        if k not in ("line_item", "selected"):
                            all_model_cols.add(k)

                columns = [{"key": "line_item", "label": "Line Item"}]
                for m in ["linear", "ets", "arima", "prophet"]:
                    if m in all_model_cols:
                        columns.append({"key": m, "label": m.upper() + " MAPE"})
                columns.append({"key": "selected", "label": "Best"})

                content_blocks.append(
                    self._table_block(
                        title="Model Comparison: MAPE Scores (★ = selected)",
                        columns=columns,
                        rows=comparison_rows[:20],  # Show top 20
                    )
                )
                if len(comparison_rows) > 20:
                    content_blocks.append(
                        self._text_block(
                            f"_Showing 20 of {len(comparison_rows)} line items. "
                            "Open the forecast table for full details._"
                        )
                    )

        # ──────────────────────────────────────────────
        # Remediation section — actionable items
        # ──────────────────────────────────────────────
        critical_items = [r for r in remediation_items if r["severity"] == "critical"]
        warning_items = [r for r in remediation_items if r["severity"] == "warning"]

        if critical_items:
            content_blocks.append(
                self._text_block(
                    f"**🔴 {len(critical_items)} Critical Items Requiring Action:**"
                )
            )
            crit_rows = [
                {
                    "line": item["line_item"],
                    "category": item["category"],
                    "score": str(round(item["score"])),
                    "issue": item["reason"],
                    "action": item["suggestion"],
                }
                for item in critical_items[:10]
            ]
            content_blocks.append(
                self._table_block(
                    title="Critical — Override or Manual Input Needed",
                    columns=[
                        {"key": "line", "label": "Line Item"},
                        {"key": "category", "label": "Category"},
                        {"key": "score", "label": "Score"},
                        {"key": "issue", "label": "Issue"},
                        {"key": "action", "label": "Recommended Action"},
                    ],
                    rows=crit_rows,
                )
            )

        if warning_items:
            content_blocks.append(
                self._text_block(
                    f"**🟡 {len(warning_items)} Items Needing Review:**"
                )
            )
            warn_rows = [
                {
                    "line": item["line_item"],
                    "category": item["category"],
                    "score": str(round(item["score"])),
                    "issue": item["reason"],
                    "action": item["suggestion"],
                }
                for item in warning_items[:8]
            ]
            content_blocks.append(
                self._table_block(
                    title="Warning — Review Recommended",
                    columns=[
                        {"key": "line", "label": "Line Item"},
                        {"key": "category", "label": "Category"},
                        {"key": "score", "label": "Score"},
                        {"key": "issue", "label": "Issue"},
                        {"key": "action", "label": "Recommended Action"},
                    ],
                    rows=warn_rows,
                )
            )
            if len(warning_items) > 8:
                content_blocks.append(
                    self._text_block(f"_...and {len(warning_items) - 8} more items needing review_")
                )

        # Show remaining warnings (non-remediation)
        if warnings:
            shown = 0
            for w in warnings:
                if shown >= 5:
                    content_blocks.append(
                        self._text_block(f"_...and {len(warnings) - shown} more warnings_")
                    )
                    break
                content_blocks.append(self._text_block(f"**Warning:** {w}"))
                shown += 1

        # Panel trigger — forecast table
        content_blocks.append(
            self._panel_trigger(
                panel="forecast_table",
                params={"version_id": version.id},
                label="View Full Forecast Table",
            )
        )

        # If there are items needing attention, also trigger review dashboard
        if critical_items or warning_items:
            content_blocks.append(
                self._panel_trigger(
                    panel="review_dashboard",
                    params={"version_id": version.id},
                    label="Open AI Review Dashboard",
                )
            )

        return SkillResult.ok(
            message=(
                f"Generated forecast {version.name}: {summary['success']} lines forecasted "
                f"in {elapsed:.1f}s. Confidence: {high_count} high, {medium_count} medium, "
                f"{low_count} low. {len(critical_items)} items need immediate action, "
                f"{len(warning_items)} need review."
            ),
            data={
                "version_id": version.id,
                "version_name": version.name,
                "lines_forecasted": summary["success"],
                "lines_skipped": summary["skipped"],
                "zero_lines": summary["zero_lines"],
                "sparse_lines": summary["sparse_lines"],
                "structural_breaks": summary["structural_breaks"],
                "model_distribution": summary["model_distribution"],
                "generation_time": elapsed,
                "warnings_count": len(warnings),
                "confidence": {
                    "high": high_count,
                    "medium": medium_count,
                    "low": low_count,
                },
                "remediation": {
                    "critical_count": len(critical_items),
                    "warning_count": len(warning_items),
                },
            },
            content_blocks=content_blocks,
        )

    def _build_timeout_response(self, version, summary, warnings, flags) -> SkillResult:
        """Build response when generation times out (EC11)."""
        return SkillResult.ok(
            message=(
                f"Forecast generation timed out after processing {summary['success']} of "
                f"{summary['total']} lines. Partial results saved as {version.name}."
            ),
            data={
                "version_id": version.id,
                "partial": True,
                "lines_completed": summary["success"],
                "lines_total": summary["total"],
            },
            content_blocks=[
                self._text_block(
                    f"**Timeout:** Forecast generation for **{version.name}** exceeded "
                    f"the {settings.max_forecast_generation_minutes}-minute limit. "
                    f"Completed {summary['success']}/{summary['total']} lines."
                ),
                self._text_block(
                    "Consider reducing the number of line items or simplifying model configuration. "
                    "Partial results have been saved."
                ),
            ],
        )
