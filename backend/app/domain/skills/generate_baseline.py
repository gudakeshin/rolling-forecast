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

import pandas as pd
from sqlalchemy.orm import Session

from app.domain.base_skill import BaseSkill, SkillContext, SkillResult
from app.domain.engines.model_registry import (
    effective_selection_rule,
    get_model_registry,
    reset_model_registry,
)
from app.models.actuals import ActualsDataset, ActualsRecord
from app.models.line_item import LineItem
from app.models.forecast import ForecastVersion, ForecastLineResult, ModelMetadata
from app.config import settings
from app.services.confidence import (
    MODEL_BASE_SCORES as _MODEL_BASE_SCORES,
    compute_confidence_score as _compute_confidence_score,
    classify_confidence as _classify_confidence,
)
from app.services.error_handlers import (
    check_generation_timeout,
    ForecastTimeoutError,
)

logger = logging.getLogger(__name__)


# Re-export for any lingering importers; prefer app.services.confidence.
__all__ = ["GenerateBaselineSkill", "_compute_confidence_score", "_classify_confidence", "_MODEL_BASE_SCORES"]


# ──────────────────────────────────────────────────────
# Remediation / recommendation helpers (scoring in confidence.py)
# ──────────────────────────────────────────────────────


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


def forecast_linked_drivers(
    db: Session,
    *,
    version_id: str,
    horizon: int,
    random_seed: int,
    model_registry,
    cal_cfg,
) -> dict[str, Any]:
    """Stage 0 (Phase 8): forecast linked drivers into driver_values(value_type='forecast').

    This keeps driver->driver recursion out of scope by using only non-exog models.
    Forecast horizon is extended by each driver's max active lag so line models can
    consume lagged future exog columns safely.
    """
    from app.models.driver import DriverLink
    from app.services.driver_series import materialize_driver_series, upsert_driver_values
    from app.services.period_calendar import period_to_date

    links = (
        db.query(DriverLink)
        .filter(DriverLink.status == "active")
        .all()
    )
    if not links:
        return {"drivers_considered": 0, "drivers_forecasted": 0, "rows_written": 0, "warnings": []}

    lag_by_driver: dict[int, int] = {}
    for link in links:
        lag_by_driver[link.driver_id] = max(lag_by_driver.get(link.driver_id, 0), int(link.lag or 0))

    candidate_models = [
        n
        for n in model_registry.list_models_by_complexity()
        if (
            (m := model_registry.get(n))
            and m.capabilities.auto_selectable
            and not m.capabilities.supports_exog
        )
    ]
    warnings: list[str] = []
    rows_written = 0
    done = 0
    min_points = 12

    for driver_id, max_lag in lag_by_driver.items():
        series = materialize_driver_series(db, driver_id=driver_id, value_type="actual")
        if series.empty or len(series) < min_points:
            continue
        periods = [str(p) for p in series.index]
        values = pd.Series(series.values.astype(float))
        dates = pd.DatetimeIndex([pd.Timestamp(period_to_date(p, cal_cfg)) for p in periods])

        try:
            best_model, _, _ = model_registry.auto_select(
                values,
                dates,
                random_seed=random_seed,
                models_to_test=candidate_models,
                selection_rule=effective_selection_rule(),
                two_stage=True,
                is_material=True,
            )
            if not best_model:
                warnings.append(f"driver {driver_id}: no eligible model")
                continue
            out = model_registry.fit_and_predict(
                best_model,
                values,
                dates,
                horizon + max_lag,
                random_seed=random_seed,
            )
            payload = []
            for i, period in enumerate(out.periods):
                payload.append(
                    {
                        "period": period,
                        "value": float(out.point_forecast[i]),
                        "p10": float(out.lower_bound[i]) if out.lower_bound is not None else None,
                        "p90": float(out.upper_bound[i]) if out.upper_bound is not None else None,
                    }
                )
            n = upsert_driver_values(
                db,
                driver_id=driver_id,
                rows=payload,
                value_type="forecast",
                version_id=version_id,
                actor=None,
            )
            rows_written += int(n)
            done += 1
        except Exception as e:
            warnings.append(f"driver {driver_id}: {str(e)[:120]}")

    return {
        "drivers_considered": len(lag_by_driver),
        "drivers_forecasted": done,
        "rows_written": rows_written,
        "warnings": warnings,
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
                    "description": (
                        "Force a single model for ALL line items, or 'auto' (default) to "
                        "run walk-forward CV per line. Valid names come from the model "
                        "registry (validated at execute time)."
                    ),
                    "default": "auto",
                },
                "models_to_test": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Subset of registry models to test during auto-selection. "
                        "If omitted, all registered models (subject to cost screen) are tested."
                    ),
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
                "scenario": {
                    "type": "string",
                    "description": "Scenario label for this version (default: base). Examples: base, upside, downside.",
                    "default": "base",
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

        # Async enqueue: default in production (or when REQUIRE_ASYNC_JOBS=true).
        # Worker passes async_job=False explicitly to run in-process.
        want_async = params.get("async_job")
        if want_async is None and settings.async_jobs_required:
            want_async = True
        if want_async:
            from app.services.job_queue import enqueue_generate_baseline

            job = await enqueue_generate_baseline(
                version_name="pending",
                params={k: v for k, v in params.items() if k != "async_job"},
                user_id=context.user_id,
                conversation_id=context.conversation_id,
            )
            if job.get("status") == "sync_required":
                if settings.async_jobs_required:
                    return SkillResult.fail(
                        job.get("message")
                        or "Async job queue unavailable (Redis/arq required in production)",
                        error="async_unavailable",
                    )
                # Dev fallback: continue synchronously
                params = {**params, "async_job": False}
            else:
                status = self._status_block(
                    label="Queued forecast generation",
                    progress=0.05,
                    step=f"job_id={job['job_id']}",
                )
                status["data"]["job_id"] = job["job_id"]
                return SkillResult.ok(
                    message=f"Baseline generation queued (job {job['job_id']})",
                    data=job,
                    content_blocks=[
                        status,
                        self._text_block(
                            f"Job `{job['job_id']}` queued. Poll `GET /api/jobs/{job['job_id']}` for status."
                        ),
                    ],
                )

        horizon = params.get("horizon_months", settings.default_horizon_months)
        model_type = params.get("model_type", "auto")
        models_to_test = params.get("models_to_test", None)  # None = all models
        random_seed = params.get("random_seed", 42)

        # Settings flips (benchmarks / metric) must refresh the singleton
        reset_model_registry()
        model_registry = get_model_registry()
        registered = set(model_registry.list_models())
        if model_type != "auto" and model_type not in registered:
            return SkillResult.fail(
                f"Unknown model_type '{model_type}'. Registered: {sorted(registered)}"
            )
        if models_to_test:
            unknown = [m for m in models_to_test if m not in registered]
            if unknown:
                return SkillResult.fail(
                    f"Unknown models_to_test {unknown}. Registered: {sorted(registered)}"
                )
        selection_rule = effective_selection_rule()

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

        scenario = (params.get("scenario") or "base").strip() or "base"
        version = ForecastVersion(
            name=version_name,
            status="draft",
            version_type="scheduled",
            scenario=scenario,
            actuals_dataset_id=dataset_id,
            actuals_hash=dataset.file_hash,
            horizon_months=horizon,
            base_period=dataset.period_end,
            random_seed=random_seed,
            selection_rule=selection_rule,
            created_by=context.user_id,  # SoD: creator cannot self-approve
        )
        # FX: resolve reporting currency up front
        from app.services.fx import get_reporting_currency, MissingFxRateError, convert_series_values
        reporting_ccy = get_reporting_currency(db)
        version.reporting_currency = reporting_ccy
        db.add(version)
        db.flush()
        fx_hashes: list[str] = []

        from app.services.period_calendar import (
            get_calendar_config,
            period_to_date,
            push_calendar,
            reset_calendar,
        )
        from app.services.reconciliation import reconcile_version

        cal_cfg = get_calendar_config(db)
        cal_token = push_calendar(cal_cfg)

        # Preload all actuals for this dataset once (avoids N+1 per line item)
        from collections import defaultdict

        actuals_by_li: dict[int, list[ActualsRecord]] = defaultdict(list)
        for rec in (
            db.query(ActualsRecord)
            .filter(ActualsRecord.dataset_id == dataset_id)
            .order_by(ActualsRecord.period)
            .all()
        ):
            actuals_by_li[rec.line_item_id].append(rec)

        # Generate forecast for each line item
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
            "selection_rule": selection_rule,
            "n_downgraded": 0,
            "driver_forecasts_written": 0,
            "drivers_forecasted": 0,
        }
        all_warnings: list[str] = []
        all_flags: list[dict] = []
        pending_line_rows: list[dict[str, Any]] = []
        pending_metadata: list[ModelMetadata] = []

        # Materiality vs CoA total trailing actuals (for two-stage expensive admit)
        trailing_abs = {
            li_id: float(sum(abs(r.value) for r in recs))
            for li_id, recs in actuals_by_li.items()
        }
        grand_abs = sum(trailing_abs.values()) or 1.0
        materiality_share = float(settings.materiality_share)

        job_id = params.get("_job_id")
        n_line_items = len(line_items)

        # Phase 8 stage 0: materialize forecasted driver paths for this version.
        if settings.enable_driver_forecasting:
            driver_stage = forecast_linked_drivers(
                db,
                version_id=version.id,
                horizon=horizon,
                random_seed=random_seed,
                model_registry=model_registry,
                cal_cfg=cal_cfg,
            )
            summary["driver_forecasts_written"] = int(driver_stage.get("rows_written", 0) or 0)
            summary["drivers_forecasted"] = int(driver_stage.get("drivers_forecasted", 0) or 0)
            all_warnings.extend(driver_stage.get("warnings") or [])

        try:
            for li in line_items:
                summary["total"] += 1

                if job_id and (n_line_items <= 5 or summary["total"] % 5 == 0):
                    from app.services.job_queue import update_job

                    update_job(
                        job_id,
                        status="running",
                        progress=min(0.9, summary["total"] / max(n_line_items, 1)),
                        step=f"{li.name} ({summary['total']}/{n_line_items})",
                    )

                # EC11: Check timeout periodically
                try:
                    check_generation_timeout(
                        start_time,
                        context=f"Processing line {summary['total']}/{len(line_items)}: {li.name}",
                    )
                except ForecastTimeoutError as e:
                    # Flush any pending rows before returning
                    self._flush_forecast_batch(db, pending_line_rows, pending_metadata)
                    version.generation_time_seconds = time.time() - start_time
                    db.commit()
                    all_warnings.append(str(e))
                    return self._build_timeout_response(version, summary, all_warnings, all_flags)

                records = actuals_by_li.get(li.id, [])

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
                dates = pd.DatetimeIndex([
                    pd.Timestamp(period_to_date(p, cal_cfg)) for p in periods
                ])

                is_material = (trailing_abs.get(li.id, 0.0) / grand_abs) >= materiality_share
                from app.services.forecast_pipeline import LineForecastContext, forecast_line_item

                line_out = forecast_line_item(
                    db,
                    li,
                    values,
                    dates,
                    periods,
                    LineForecastContext(
                        version_id=version.id,
                        horizon=horizon,
                        random_seed=random_seed,
                        model_type=model_type,
                        models_to_test=models_to_test,
                        selection_rule=selection_rule,
                        is_material=is_material,
                        cal_cfg=cal_cfg,
                        model_registry=model_registry,
                        enable_driver_forecasting=settings.enable_driver_forecasting,
                    ),
                )

                all_warnings.extend(line_out.warnings)
                if line_out.flags:
                    all_flags.append({"line_item": li.name, "flags": line_out.flags})
                if line_out.was_zero:
                    summary["zero_lines"] += 1
                if line_out.was_sparse:
                    summary["sparse_lines"] += 1
                if line_out.had_structural_break:
                    summary["structural_breaks"] += 1
                if line_out.was_clamped:
                    summary["negative_clamped"] += 1

                if line_out.skipped:
                    summary["skipped"] += 1
                    continue

                if line_out.comparison:
                    summary["model_comparisons"][li.name] = line_out.comparison
                summary["n_downgraded"] += line_out.n_downgraded
                if line_out.selected_model:
                    summary["model_distribution"][line_out.selected_model] = (
                        summary["model_distribution"].get(line_out.selected_model, 0) + 1
                    )

                pending_line_rows.extend(line_out.line_rows)
                pending_metadata.extend(line_out.metadata_rows)

                if len(pending_line_rows) >= 1000:
                    self._flush_forecast_batch(db, pending_line_rows, pending_metadata)
                    pending_line_rows.clear()
                    pending_metadata.clear()

                summary["success"] += 1

        except Exception as e:
            logger.error(f"Forecast generation error: {e}", exc_info=True)
            all_warnings.append(f"Generation error: {str(e)[:200]}")
        finally:
            reset_calendar(cal_token)

        # Persist batched forecast rows before reconciliation / scoring
        self._flush_forecast_batch(db, pending_line_rows, pending_metadata)
        pending_line_rows.clear()
        pending_metadata.clear()

        # Reconcile CoA parents (p50 identities + full MinT correlation-aware bounds)
        try:
            recon = reconcile_version(db, version.id)
            logger.info("MinT reconciliation: %s", recon)
        except Exception as e:
            logger.warning("Reconciliation skipped: %s", e)
            all_warnings.append(f"CoA reconciliation skipped: {str(e)[:120]}")

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

        if job_id:
            from app.services.job_queue import update_job

            update_job(job_id, status="running", progress=0.95, step="Finalizing")

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
                    {"metric": "Drivers forecasted (stage 0)", "value": str(summary.get("drivers_forecasted", 0))},
                    {"metric": "Driver forecast rows written", "value": str(summary.get("driver_forecasts_written", 0))},
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
                    model_key = c.get("model") or c.get("model_name")
                    if not model_key:
                        continue
                    mape_val = c.get("mape")
                    if mape_val is not None:
                        label = f"{mape_val:.1f}%"
                        if c.get("selected"):
                            label += " ★"
                    elif c.get("error"):
                        label = "N/A"
                    else:
                        label = "-"
                    row[model_key] = label
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

        actions = [
            {
                "id": "open_forecast_table",
                "label": "View Forecast Table",
                "variant": "primary",
                "panel": "forecast_table",
                "version_id": version.id,
            },
        ]
        if critical_items or warning_items:
            actions.append({
                "id": "open_review_dashboard",
                "label": "Open Review Dashboard",
                "panel": "review_dashboard",
                "version_id": version.id,
            })
        else:
            actions.append({
                "id": "submit_for_review",
                "label": "Submit for Review",
                "variant": "secondary",
                "version_id": version.id,
            })
        content_blocks.append(self._action_block(actions))

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
                "drivers_forecasted": summary.get("drivers_forecasted", 0),
                "driver_forecasts_written": summary.get("driver_forecasts_written", 0),
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

    @staticmethod
    def _flush_forecast_batch(
        db: Session,
        line_rows: list[dict[str, Any]],
        metadata_rows: list[ModelMetadata],
    ) -> None:
        """Bulk-upsert forecast line results then attach model metadata."""
        if not line_rows:
            return
        from app.services.upsert import bulk_upsert

        bulk_upsert(
            db,
            ForecastLineResult,
            line_rows,
            conflict_cols=("version_id", "line_item_id", "period"),
            update_cols=(
                "p10",
                "p50",
                "p90",
                "model_p50",
                "confidence_score",
                "confidence_level",
                "model_type",
                "model_mape",
                "model_mase",
                "model_pinball",
                "model_r_squared",
                "bounds_method",
                "is_overridden",
                "is_calculated",
            ),
        )
        for meta in metadata_rows:
            db.add(meta)
        db.flush()

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
