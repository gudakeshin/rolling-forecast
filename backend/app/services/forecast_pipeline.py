"""Per-line-item forecast pipeline extracted from GenerateBaselineSkill.

Pure orchestration around registry models — no skill/LLM coupling. Keeping this
isolated is what makes Phase 3/8 changes safe (characterization-tested).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from app.config import settings
from app.domain.engines.base_model import make_period_labels, mase_denominator
from app.domain.engines.model_registry import ModelRegistry, get_model_registry
from app.models.forecast import ModelMetadata
from app.models.line_item import LineItem
from app.services.driver_exog import build_exog_for_line
from app.services.error_handlers import HistoryAnalysis, clamp_forecast_values
from app.services.outlier_cleaning import clean_series_for_fit
from app.services.period_calendar import FiscalCalendarConfig, period_to_date
from app.services.reconciliation import BOUNDS_METHOD_MODEL

logger = logging.getLogger(__name__)


def _mean_finite(values: list[float]) -> float | None:
    finite = [v for v in values if np.isfinite(v)]
    if not finite:
        return None
    return float(np.mean(finite))


def _evaluate_arima_exog_modes(
    registry: ModelRegistry,
    *,
    line_item_id: int,
    values: pd.Series,
    dates: pd.DatetimeIndex,
    periods: list[str],
    version_id: str,
    db: Session,
) -> dict[str, Any] | None:
    """Compute fold-level MASE under exog_mode=forecast vs exog_mode=known.

    - forecast: driver precedence uses version-scoped overlays (plan/scenario/forecast)
    - known: uses actual driver values only
    """
    model = registry.get("arima")
    if model is None:
        return None

    bundle_forecast = build_exog_for_line(
        db,
        line_item_id=line_item_id,
        train_periods=periods,
        future_periods=[],
        version_id=version_id,
        max_links=3,
    )
    bundle_known = build_exog_for_line(
        db,
        line_item_id=line_item_id,
        train_periods=periods,
        future_periods=[],
        version_id=None,
        max_links=3,
    )
    if bundle_forecast is None or bundle_known is None:
        return None
    if bundle_forecast.exog_train.shape[1] == 0 or bundle_known.exog_train.shape[1] == 0:
        return None

    n = len(values)
    m = 12
    fold_h = 3
    n_folds = 3
    mase_forecast: list[float] = []
    mase_known: list[float] = []
    used = 0

    for fold in range(1, n_folds + 1):
        holdout = fold_h * fold
        train_end = n - holdout
        if train_end < max(18, model.min_data_points):
            break
        test_start = train_end
        test_end = min(train_end + fold_h, n)
        if test_end <= test_start:
            break

        y_train = values.iloc[:train_end]
        y_test = np.asarray(values.iloc[test_start:test_end].values, dtype=float)
        d_train = dates[:train_end]

        ex_f_train = bundle_forecast.exog_train.iloc[:train_end]
        ex_f_test = bundle_forecast.exog_train.iloc[test_start:test_end]
        ex_k_train = bundle_known.exog_train.iloc[:train_end]
        ex_k_test = bundle_known.exog_train.iloc[test_start:test_end]

        denom, _ = mase_denominator(np.asarray(y_train.values, dtype=float), m)
        if denom <= 1e-12:
            continue

        try:
            p_f = model.fit(y_train, d_train, exog=ex_f_train)
            fc_f = model.predict(p_f, len(y_test), d_train[-1], exog_future=ex_f_test)
            err_f = np.abs(y_test - np.asarray(fc_f.point_forecast[: len(y_test)], dtype=float))
            mase_forecast.append(float(np.mean(err_f) / denom))
        except Exception:
            mase_forecast.append(float("inf"))

        try:
            p_k = model.fit(y_train, d_train, exog=ex_k_train)
            fc_k = model.predict(p_k, len(y_test), d_train[-1], exog_future=ex_k_test)
            err_k = np.abs(y_test - np.asarray(fc_k.point_forecast[: len(y_test)], dtype=float))
            mase_known.append(float(np.mean(err_k) / denom))
        except Exception:
            mase_known.append(float("inf"))
        used += 1

    if used == 0:
        return None
    return {
        "n_folds": used,
        "mase_forecast": _mean_finite(mase_forecast),
        "mase_known": _mean_finite(mase_known),
    }


@dataclass
class LineForecastContext:
    """Shared inputs for one baseline run (version-level)."""

    version_id: str
    horizon: int
    random_seed: int = 42
    model_type: str = "auto"
    models_to_test: list[str] | None = None
    selection_rule: str | None = None
    is_material: bool | None = None
    cal_cfg: FiscalCalendarConfig | None = None
    model_registry: ModelRegistry | None = None
    enable_driver_forecasting: bool | None = None


@dataclass
class LineForecast:
    """Batched outputs for one line item — caller upserts."""

    line_item_id: int
    line_item_name: str
    skipped: bool = False
    skip_reason: str | None = None
    selected_model: str | None = None
    honest_mape: float | None = None
    selection_mase: float | None = None
    selection_pinball: float | None = None
    comparison: dict[str, Any] | None = None
    n_downgraded: int = 0
    line_rows: list[dict[str, Any]] = field(default_factory=list)
    metadata_rows: list[ModelMetadata] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    # Counters mirroring generate_baseline summary buckets
    was_zero: bool = False
    was_sparse: bool = False
    had_structural_break: bool = False
    was_clamped: bool = False


def forecast_line_item(
    db: Session,
    li: LineItem,
    values: pd.Series,
    dates: pd.DatetimeIndex,
    periods: list[str],
    ctx: LineForecastContext,
) -> LineForecast:
    """Fit/select/predict one line item into batched row dicts + ModelMetadata.

    ``values`` / ``dates`` / ``periods`` must already be FX-converted and
    calendar-aligned. Does not write to the DB.
    """
    out = LineForecast(line_item_id=li.id, line_item_name=li.name)
    if values is None or len(values) == 0:
        out.skipped = True
        out.skip_reason = "no_actuals"
        return out

    registry = ctx.model_registry or get_model_registry()
    cal_cfg = ctx.cal_cfg
    horizon = ctx.horizon
    random_seed = ctx.random_seed
    model_type = ctx.model_type
    selection_rule = ctx.selection_rule

    analysis = HistoryAnalysis(values, dates, li.name)
    analysis_result = analysis.analyze()
    out.warnings.extend(analysis_result.get("warnings") or [])
    out.flags = list(analysis_result.get("flags") or [])

    # EC2 / EC1 — force registry models instead of bespoke branches
    forced: str | None = None
    if analysis.is_all_zeros:
        forced = "zero"
        out.was_zero = True
    elif analysis.is_very_sparse:
        forced = "average"
        out.was_sparse = True

    effective_values = values
    effective_dates = dates
    min_post = settings.structural_break_min_post_points
    if analysis.has_structural_break and forced is None:
        out.had_structural_break = True
        break_idx = analysis.structural_break_index
        if break_idx is None and analysis.structural_break_period:
            for idx, d in enumerate(dates):
                if d.strftime("%Y-%m") == analysis.structural_break_period:
                    break_idx = idx
                    break
        if break_idx is not None and (len(values) - break_idx) >= min_post:
            effective_values = values[break_idx:]
            effective_dates = dates[break_idx:]
        else:
            out.warnings.append(
                f"Structural break flagged for '{li.name}' but post-break "
                f"history < {min_post} periods — retaining full series."
            )

    cleaning = clean_series_for_fit(
        effective_values,
        effective_dates,
        enabled=settings.outlier_cleaning_enabled and forced is None,
        mad_z=settings.outlier_mad_z,
    )
    effective_values = cleaning.cleaned
    if cleaning.n_cleaned:
        out.warnings.append(
            f"Winsorized {cleaning.n_cleaned} outlier(s) for '{li.name}' "
            f"({', '.join(cleaning.cleaned_periods[:5])}"
            f"{'…' if cleaning.n_cleaned > 5 else ''})."
        )

    selection_mape: float | None = None
    selection_mase: float | None = None
    selection_pinball: float | None = None

    try:
        effective_model_type = forced or model_type
        if forced is None and analysis.is_sparse and model_type == "auto":
            effective_model_type = "linear"

        if effective_model_type == "auto":
            selected_model, selection_mape, comparison = registry.auto_select(
                effective_values,
                effective_dates,
                random_seed=random_seed,
                models_to_test=ctx.models_to_test,
                selection_rule=selection_rule,
                is_material=ctx.is_material,
            )
            out.comparison = comparison.to_dict()
            out.n_downgraded = comparison.n_downgraded
            selection_mase = (
                comparison.best_mase if comparison.best_mase != float("inf") else None
            )
            selection_pinball = (
                comparison.best_pinball
                if comparison.best_pinball != float("inf")
                else None
            )
            if not selected_model:
                raise ValueError(
                    "No eligible model produced a finite score — refusing to "
                    "default to linear"
                )
        else:
            selected_model = effective_model_type
            model = registry.get(selected_model)
            if model is None:
                raise ValueError(f"Unknown model '{selected_model}'")
            # zero/average: skip CV (deterministic, no useful MAPE)
            if selected_model in ("zero", "average"):
                selection_mape = None
                out.comparison = {
                    "best_model": selected_model,
                    "best_mape": None,
                    "selection_method": "forced_edge_case",
                    "selection_rule": selection_rule,
                    "comparisons": [{
                        "model": selected_model,
                        "model_name": selected_model,
                        "selected": True,
                        "eligible": True,
                    }],
                }
            else:
                try:
                    cv = model.evaluate_cv(
                        effective_values, effective_dates, n_folds=3, fold_horizon=3
                    )
                    selection_mape = float(cv.get("mean_mape") or 0.0)
                    raw_mase = cv.get("mean_mase")
                    selection_mase = (
                        float(raw_mase)
                        if raw_mase is not None and raw_mase != float("inf")
                        else None
                    )
                    raw_pb = cv.get("mean_pinball")
                    selection_pinball = (
                        float(raw_pb)
                        if raw_pb is not None and raw_pb != float("inf")
                        else None
                    )
                    out.comparison = {
                        "best_model": selected_model,
                        "best_mape": selection_mape,
                        "best_mase": selection_mase,
                        "best_pinball": selection_pinball,
                        "selection_method": "rolling_origin_cv",
                        "selection_rule": selection_rule,
                        "comparisons": [{
                            "model": selected_model,
                            "model_name": selected_model,
                            "mape": selection_mape,
                            "mase": selection_mase,
                            "pinball": selection_pinball,
                            "fold_mapes": cv.get("fold_mapes") or [],
                            "fold_mases": cv.get("fold_mases") or [],
                            "n_folds": cv.get("n_folds_used") or 0,
                            "selected": True,
                            "eligible": True,
                        }],
                    }
                except Exception as cv_err:
                    logger.warning(
                        "CV failed for forced model %s on %s: %s",
                        selected_model, li.name, cv_err,
                    )
                    selection_mape = None

        exog_bundle = None
        use_exog = False
        exog_spec: dict[str, Any] | None = None
        exog_enabled = (
            settings.enable_driver_forecasting
            if ctx.enable_driver_forecasting is None
            else bool(ctx.enable_driver_forecasting)
        )
        if (
            exog_enabled
            and selected_model
            and registry.get(selected_model) is not None
            and registry.get(selected_model).capabilities.supports_exog
        ):
            future_periods = make_period_labels(effective_dates[-1], horizon)
            exog_bundle = build_exog_for_line(
                db,
                line_item_id=li.id,
                train_periods=[str(p) for p in periods[-len(effective_values):]],
                future_periods=future_periods,
                version_id=ctx.version_id,
                max_links=3,
            )
            if exog_bundle is not None:
                min_train = len(effective_values) - 9  # 3 folds × horizon 3
                k = int(exog_bundle.exog_train.shape[1])
                needed = int(settings.exog_min_points_per_regressor) * max(k, 1)
                if min_train >= needed:
                    use_exog = True
                    exog_spec = {
                        "mode": "forecast",
                        "drivers": [
                            {
                                "driver_id": s.driver_id,
                                "driver_key": s.driver_key,
                                "relation": s.relation,
                                "lag": s.lag,
                            }
                            for s in exog_bundle.specs
                        ],
                        "columns": list(exog_bundle.exog_train.columns),
                        "min_train_points": min_train,
                        "required_points": needed,
                        "exog_source_version_id": ctx.version_id,
                    }
                    mode_scores = _evaluate_arima_exog_modes(
                        registry,
                        line_item_id=li.id,
                        values=effective_values,
                        dates=effective_dates,
                        periods=[str(p) for p in periods[-len(effective_values):]],
                        version_id=ctx.version_id,
                        db=db,
                    )
                    exog_spec["exog_mode_scores"] = mode_scores or {
                        "n_folds": 0,
                        "mase_forecast": None,
                        "mase_known": None,
                    }
                else:
                    out.warnings.append(
                        f"{li.name}: exog not admitted (effective train {min_train} < {needed})"
                    )

        forecast_output = registry.fit_and_predict(
            selected_model,
            effective_values,
            effective_dates,
            horizon,
            random_seed=random_seed,
            exog=(exog_bundle.exog_train if use_exog and exog_bundle is not None else None),
            exog_future=(exog_bundle.exog_future if use_exog and exog_bundle is not None else None),
        )
        if use_exog and exog_spec is not None:
            forecast_output.parameters = dict(forecast_output.parameters or {})
            forecast_output.parameters["exog_spec"] = exog_spec
            if out.comparison is not None:
                out.comparison["exog_mode_scores"] = exog_spec.get("exog_mode_scores")

        cv_mape = selection_mape if selection_mape is not None else None
        in_sample = (
            forecast_output.fit_metrics.get("in_sample_mape")
            or forecast_output.fit_metrics.get("mape")
        )
        if cv_mape is not None and cv_mape != float("inf"):
            honest_mape = cv_mape
        else:
            honest_mape = None
            if in_sample is not None and selected_model not in ("zero", "average"):
                forecast_output.fit_metrics["in_sample_mape"] = in_sample
                out.warnings.append(
                    f"{li.name}: CV MAPE unavailable; confidence uses "
                    "non-MAPE signals only (in-sample MAPE withheld)"
                )

        point_forecast = forecast_output.point_forecast.copy()
        lower_bound = (
            forecast_output.lower_bound.copy()
            if forecast_output.lower_bound is not None
            else None
        )

        point_forecast, clamp_warnings = clamp_forecast_values(
            point_forecast, li.allow_negative, li.name
        )
        if clamp_warnings:
            out.was_clamped = True
            out.warnings.extend(clamp_warnings)

        if lower_bound is not None and not li.allow_negative:
            lower_bound = np.maximum(lower_bound, 0)

        out.selected_model = selected_model
        out.honest_mape = honest_mape
        out.selection_mase = selection_mase
        out.selection_pinball = selection_pinball

        for i, period in enumerate(forecast_output.periods):
            p50 = float(point_forecast[i])
            p10 = float(lower_bound[i]) if lower_bound is not None else None
            p90 = (
                float(forecast_output.upper_bound[i])
                if forecast_output.upper_bound is not None
                else None
            )
            result_id = str(uuid.uuid4())
            out.line_rows.append({
                "id": result_id,
                "version_id": ctx.version_id,
                "line_item_id": li.id,
                "period": period,
                "p10": p10,
                "p50": p50,
                "p90": p90,
                "model_p50": p50,
                "confidence_score": 0,
                "confidence_level": "pending",
                "model_type": selected_model,
                "model_mape": honest_mape,
                "model_mase": selection_mase,
                "model_pinball": selection_pinball,
                "model_r_squared": forecast_output.fit_metrics.get("r_squared"),
                "bounds_method": BOUNDS_METHOD_MODEL,
                "is_overridden": False,
                "is_calculated": False,
            })
            if i == 0:
                out.metadata_rows.append(
                    ModelMetadata(
                        line_result_id=result_id,
                        model_type=selected_model,
                        parameters=forecast_output.parameters,
                        training_window_start=periods[0] if periods else None,
                        training_window_end=periods[-1] if periods else None,
                        training_points=len(effective_values),
                        mape=honest_mape,
                        r_squared=forecast_output.fit_metrics.get("r_squared"),
                        aic=forecast_output.fit_metrics.get("aic"),
                        seasonality_detected=forecast_output.diagnostics.get(
                            "seasonality_detected", False
                        ),
                        seasonality_period=forecast_output.diagnostics.get(
                            "seasonality_period"
                        ),
                        structural_break_detected=analysis.has_structural_break,
                        structural_break_period=analysis.structural_break_period,
                        cleaned_periods=cleaning.cleaned_periods or None,
                        outliers_cleaned=cleaning.n_cleaned,
                        random_seed=random_seed,
                    )
                )
    except Exception as e:
        logger.error("Forecast failed for %s: %s", li.account_code, e, exc_info=True)
        out.skipped = True
        out.skip_reason = str(e)[:200]
        out.warnings.append(f"Model failed for '{li.name}': {str(e)[:100]}")

    return out


def build_series_from_records(
    records: list[Any],
    cal_cfg: FiscalCalendarConfig,
) -> tuple[pd.Series, pd.DatetimeIndex, list[str]]:
    """Helper for callers that already hold FX-converted ActualsRecord-like rows.

    Expects each record to expose ``.value`` and ``.period`` (converted value).
    """
    periods = [r.period for r in records]
    values = pd.Series([float(r.value) for r in records])
    dates = pd.DatetimeIndex([pd.Timestamp(period_to_date(p, cal_cfg)) for p in periods])
    return values, dates, periods
