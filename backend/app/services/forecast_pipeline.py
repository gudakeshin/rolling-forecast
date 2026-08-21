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
from app.domain.engines.base_model import as_exog_model, make_period_labels, mase_denominator
from app.domain.engines.model_registry import ModelRegistry, get_model_registry
from app.models.forecast import ModelMetadata
from app.models.line_item import LineItem
from app.services.driver_exog import build_exog_for_line
from app.services.error_handlers import HistoryAnalysis, clamp_forecast_values
from app.services.outlier_cleaning import clean_series_for_fit
from app.services.period_calendar import FiscalCalendarConfig, period_to_date
from app.services.reconciliation import BOUNDS_METHOD_MODEL
from app.services.reflection import active_heuristics_for_line, heuristic_summary

logger = logging.getLogger(__name__)


def _mean_finite(values: list[float]) -> float | None:
    finite = [v for v in values if np.isfinite(v)]
    if not finite:
        return None
    return float(np.mean(finite))


def _active_heuristics(db: Session | None, line_item_id: int) -> list[Any]:
    """Consumable active heuristics for a line — never fatal to a forecast."""
    if db is None:
        return []
    try:
        return active_heuristics_for_line(db, line_item_id)
    except Exception as e:  # pragma: no cover — legacy schemas without the table
        logger.debug("active heuristic lookup failed for line %s: %s", line_item_id, e)
        return []


def _heuristic_nudge(
    comparison: Any,
    heuristics: list[Any],
    *,
    selected_model: str | None,
    allow_override: bool,
) -> dict[str, Any] | None:
    """Decide whether an active heuristic should redirect model selection.

    An error-bias heuristic names the model that produced the bias, which is a
    weak preference signal at best — so it may only pick a *statistical tie*.
    The candidate has to be eligible and land inside the 1-SE band of the best
    model's fold MASE, the same tolerance the complexity tie-break uses.

    Returns ``None`` when no heuristic names a usable alternative. Otherwise
    returns the decision, with ``applied`` false when ``allow_override`` is off
    (target-bearing lines) — the caller then warns instead of switching.
    """
    from app.domain.engines.model_registry import _selection_band

    named = {
        h.model_type: h
        for h in heuristics
        if h.model_type and h.model_type != selected_model
    }
    if not named:
        return None

    by_model = {c.model_name: c for c in comparison.comparisons}
    best = by_model.get(comparison.best_model)
    if best is None or best.mase == float("inf"):
        return None
    band = _selection_band(best.fold_mases, best.n_folds)

    for model_type, heuristic in named.items():
        candidate = by_model.get(model_type)
        if candidate is None or not candidate.eligible:
            continue
        if candidate.mase == float("inf"):
            continue
        if candidate.mase > best.mase + band:
            continue
        return {
            "applied": allow_override,
            "reason": "within_1se_of_best" if allow_override else "target_bearing_line",
            "heuristic_id": heuristic.id,
            "heuristic_statement": heuristic.statement,
            "from_model": comparison.best_model,
            "to_model": model_type,
            "best_mase": round(float(best.mase), 4),
            "candidate_mase": round(float(candidate.mase), 4),
            "band": round(float(band), 4),
        }
    return None


def _exog_admission_band(mase_plain: float, fold_diffs: list[float]) -> float:
    """1-SE band on fold MASE differences, floored at 5% of plain MASE."""
    floor = 0.05 * max(abs(mase_plain), 1e-6)
    finite = [d for d in fold_diffs if np.isfinite(d)]
    if len(finite) < 2:
        return floor
    se = float(np.std(finite, ddof=1) / np.sqrt(len(finite)))
    return max(se, floor)


def _evaluate_plain_vs_exog(
    registry: ModelRegistry,
    *,
    model_name: str,
    values: pd.Series,
    dates: pd.DatetimeIndex,
    exog_train: pd.DataFrame,
) -> dict[str, Any] | None:
    """Fold MASE for plain vs exog variants of the same model.

    Returns mean MASEs, fold diffs, and whether exog clears the 1-SE admission band.
    """
    model = registry.get(model_name)
    if model is None or not model.capabilities.supports_exog:
        return None
    if exog_train is None or exog_train.shape[1] == 0:
        return None
    # Guarded above, so this only narrows the type — it cannot raise here.
    exog_model = as_exog_model(model)

    n = len(values)
    m = 12
    fold_h = 3
    n_folds = 3
    mase_plain: list[float] = []
    mase_exog: list[float] = []
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
        ex_tr = exog_train.iloc[:train_end]
        ex_te = exog_train.iloc[test_start:test_end]

        denom, _ = mase_denominator(np.asarray(y_train.values, dtype=float), m)
        if denom <= 1e-12:
            continue

        try:
            p_plain = exog_model.fit(y_train, d_train, exog=None)
            fc_plain = exog_model.predict(p_plain, len(y_test), d_train[-1], exog_future=None)
            err_p = np.abs(y_test - np.asarray(fc_plain.point_forecast[: len(y_test)], dtype=float))
            mase_plain.append(float(np.mean(err_p) / denom))
        except Exception:
            mase_plain.append(float("inf"))

        try:
            p_ex = exog_model.fit(y_train, d_train, exog=ex_tr)
            fc_ex = exog_model.predict(p_ex, len(y_test), d_train[-1], exog_future=ex_te)
            err_e = np.abs(y_test - np.asarray(fc_ex.point_forecast[: len(y_test)], dtype=float))
            mase_exog.append(float(np.mean(err_e) / denom))
        except Exception:
            mase_exog.append(float("inf"))
        used += 1

    if used == 0:
        return None

    mean_plain = _mean_finite(mase_plain)
    mean_exog = _mean_finite(mase_exog)
    if mean_plain is None or mean_exog is None:
        return {
            "n_folds": used,
            "mase_plain": mean_plain,
            "mase_exog": mean_exog,
            "exog_admitted": False,
            "exog_rejected_reason": "non_finite_mase",
            "admission_band": None,
        }

    diffs = [
        (mase_plain[i] - mase_exog[i])
        for i in range(min(len(mase_plain), len(mase_exog)))
        if np.isfinite(mase_plain[i]) and np.isfinite(mase_exog[i])
    ]
    band = _exog_admission_band(mean_plain, diffs)
    # Admit only when exog beats plain by more than the band (winner's-curse guard)
    admitted = mean_exog < (mean_plain - band)
    reason = None if admitted else "insufficient_improvement_vs_plain"
    if mean_exog >= mean_plain:
        reason = "exog_mase_not_better"
    return {
        "n_folds": used,
        "mase_plain": round(mean_plain, 6),
        "mase_exog": round(mean_exog, 6),
        "admission_band": round(band, 6),
        "exog_admitted": admitted,
        "exog_rejected_reason": reason,
        "fold_mase_plain": [round(v, 6) if np.isfinite(v) else None for v in mase_plain],
        "fold_mase_exog": [round(v, 6) if np.isfinite(v) else None for v in mase_exog],
    }


def _evaluate_arima_exog_modes(
    registry: ModelRegistry,
    *,
    line_item_id: int,
    values: pd.Series,
    dates: pd.DatetimeIndex,
    periods: list[str],
    version_id: str,
    db: Session,
    cal_cfg: FiscalCalendarConfig | None = None,
    random_seed: int = 42,
) -> dict[str, Any] | None:
    """Fold MASE under exog_mode=forecast vs known.

    - known: holdout exog uses actual driver values
    - forecast: holdout exog uses driver forecasts fit only on data ≤ fold origin
      (no look-ahead into post-origin actuals)
    """
    from app.services.driver_forecast_cache import build_origin_overlays

    model = registry.get("arima")
    if model is None:
        return None
    # ARIMA is the only exog-capable engine; assert that explicitly rather than
    # letting a TypeError get swallowed by the per-fold `except Exception`.
    exog_model = as_exog_model(model)

    # Specs / train matrix from known actuals (no leakage in training block)
    bundle_known = build_exog_for_line(
        db,
        line_item_id=line_item_id,
        train_periods=periods,
        future_periods=[],
        version_id=None,
        max_links=3,
    )
    if bundle_known is None or bundle_known.exog_train.shape[1] == 0:
        return None

    driver_ids = [s.driver_id for s in bundle_known.specs]
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
        train_periods = [str(p) for p in periods[:train_end]]
        holdout_periods = [str(p) for p in periods[test_start:test_end]]
        origin = train_periods[-1]

        ex_k_train = bundle_known.exog_train.iloc[:train_end]
        ex_k_test = bundle_known.exog_train.iloc[test_start:test_end]

        overlays = build_origin_overlays(
            db,
            driver_ids=driver_ids,
            train_end_period=origin,
            future_periods=holdout_periods,
            registry=registry,
            cal_cfg=cal_cfg,
            random_seed=random_seed,
        )
        bundle_f = build_exog_for_line(
            db,
            line_item_id=line_item_id,
            train_periods=train_periods,
            future_periods=holdout_periods,
            version_id=None,  # train on actuals only
            max_links=3,
            overlays=overlays,
        )
        if bundle_f is None:
            mase_forecast.append(float("inf"))
        else:
            ex_f_train = bundle_f.exog_train
            ex_f_test = bundle_f.exog_future
            # Align columns with known bundle when possible
            common = [c for c in ex_k_train.columns if c in ex_f_train.columns]
            if not common:
                mase_forecast.append(float("inf"))
            else:
                try:
                    p_f = exog_model.fit(y_train, d_train, exog=ex_f_train[common])
                    fc_f = exog_model.predict(
                        p_f, len(y_test), d_train[-1], exog_future=ex_f_test[common]
                    )
                    err_f = np.abs(
                        y_test - np.asarray(fc_f.point_forecast[: len(y_test)], dtype=float)
                    )
                    denom, _ = mase_denominator(np.asarray(y_train.values, dtype=float), m)
                    mase_forecast.append(
                        float(np.mean(err_f) / denom) if denom > 1e-12 else float("inf")
                    )
                except Exception:
                    mase_forecast.append(float("inf"))

        denom, _ = mase_denominator(np.asarray(y_train.values, dtype=float), m)
        if denom <= 1e-12:
            continue

        try:
            p_k = exog_model.fit(y_train, d_train, exog=ex_k_train)
            fc_k = exog_model.predict(p_k, len(y_test), d_train[-1], exog_future=ex_k_test)
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
        "origin_restricted": True,
    }


def _inflate_exog_intervals(
    db: Session,
    *,
    point: np.ndarray,
    lower: np.ndarray,
    upper: np.ndarray,
    periods: list[str],
    betas: dict[str, float],
    specs: list[Any],
    version_id: str,
) -> dict[str, Any] | None:
    """Widen predictive intervals by delta-method driver uncertainty Σ β² Var(D)."""
    from app.models.driver import DriverValue

    if not betas or not specs:
        return None

    # Map column → driver_id
    col_to_driver = {f"d{s.driver_id}_lag{int(s.lag)}": int(s.driver_id) for s in specs}
    driver_ids = sorted({int(s.driver_id) for s in specs})
    if not driver_ids:
        return None

    # Load p10/p90 for forecast/actual values covering horizon periods
    rows = (
        db.query(DriverValue)
        .filter(
            DriverValue.driver_id.in_(driver_ids),
            DriverValue.period.in_(periods),
            DriverValue.value_type.in_(["forecast", "actual", "plan", "scenario"]),
        )
        .all()
    )
    # Prefer version-scoped forecast, then any
    by_key: dict[tuple[int, str], DriverValue] = {}
    for r in rows:
        key = (int(r.driver_id), str(r.period))
        existing = by_key.get(key)
        if existing is None:
            by_key[key] = r
            continue
        # Prefer matching version_id forecast rows
        if r.value_type == "forecast" and r.version_id == version_id:
            by_key[key] = r
        elif existing.value_type != "forecast" and r.value_type == "forecast":
            by_key[key] = r

    Z = 1.28  # ~80% normal half-width
    new_lower = np.asarray(lower, dtype=float).copy()
    new_upper = np.asarray(upper, dtype=float).copy()
    point_arr = np.asarray(point, dtype=float)
    total_extra = 0.0
    steps_inflated = 0

    for i, period in enumerate(periods):
        extra_var = 0.0
        for col, beta in betas.items():
            did = col_to_driver.get(str(col))
            if did is None:
                continue
            dv = by_key.get((did, str(period)))
            if dv is None or dv.p10 is None or dv.p90 is None:
                continue
            half = (float(dv.p90) - float(dv.p10)) / (2.0 * Z)
            extra_var += float(beta) ** 2 * max(half, 0.0) ** 2
        if extra_var <= 0:
            continue
        model_half = max(
            abs(float(point_arr[i]) - float(new_lower[i])),
            abs(float(new_upper[i]) - float(point_arr[i])),
            0.0,
        )
        new_half = float(np.sqrt(model_half**2 + extra_var))
        new_lower[i] = float(point_arr[i]) - new_half
        new_upper[i] = float(point_arr[i]) + new_half
        total_extra += extra_var
        steps_inflated += 1

    if steps_inflated == 0:
        return None
    return {
        "lower": new_lower,
        "upper": new_upper,
        "meta": {
            "steps_inflated": steps_inflated,
            "mean_extra_var": round(total_extra / steps_inflated, 6),
            "betas": {k: round(float(v), 6) for k, v in betas.items()},
        },
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
    selection_result: Any | None = None

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
            selection_result = comparison
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

        # M3 — active heuristics are advisory. They are stamped on every line so
        # a reviewer can see what the system believes, but only a line that does
        # not carry a target may have its selection redirected: letting learned
        # rules move target-bearing lines teaches the system to hit targets
        # rather than to forecast. Override-derived heuristics are excluded
        # upstream, so nothing here can feed reviewer habit back into a model.
        active_heuristics = _active_heuristics(db, li.id)
        heuristic_meta = [heuristic_summary(h) for h in active_heuristics]
        heuristic_nudge: dict[str, Any] | None = None
        if active_heuristics and selection_result is not None:
            heuristic_nudge = _heuristic_nudge(
                selection_result,
                active_heuristics,
                selected_model=selected_model,
                allow_override=li.is_target_bearing is False,
            )
        if heuristic_nudge is not None and selection_result is not None:
            statistical_best = heuristic_nudge["from_model"]
            if heuristic_nudge["applied"]:
                selected_model = heuristic_nudge["to_model"]
                entry = next(
                    (
                        c
                        for c in selection_result.comparisons
                        if c.model_name == selected_model
                    ),
                    None,
                )
                if entry is not None:
                    selection_mape = (
                        float(entry.mape) if entry.mape != float("inf") else None
                    )
                    selection_mase = (
                        float(entry.mase) if entry.mase != float("inf") else None
                    )
                    selection_pinball = (
                        float(entry.pinball) if entry.pinball != float("inf") else None
                    )
                if out.comparison is not None:
                    out.comparison["best_model"] = selected_model
                    out.comparison["statistical_best_model"] = statistical_best
                    for row in out.comparison.get("comparisons") or []:
                        row["selected"] = row.get("model") == selected_model
                out.warnings.append(
                    f"{li.name}: heuristic nudge — using {selected_model} instead "
                    f"of {statistical_best} (MASE {heuristic_nudge['candidate_mase']} "
                    f"vs {heuristic_nudge['best_mase']}, within 1-SE band "
                    f"{heuristic_nudge['band']}; heuristic "
                    f"#{heuristic_nudge['heuristic_id']})"
                )
            else:
                out.warnings.append(
                    f"{li.name}: heuristic #{heuristic_nudge['heuristic_id']} "
                    f"prefers {heuristic_nudge['to_model']} over {statistical_best} "
                    "— not applied (target-bearing line)"
                )
            if out.comparison is not None:
                out.comparison["heuristic_nudge"] = heuristic_nudge
        if out.comparison is not None and heuristic_meta:
            out.comparison["active_heuristics"] = heuristic_meta

        exog_bundle = None
        use_exog = False
        exog_spec: dict[str, Any] | None = None
        exog_enabled = (
            settings.enable_driver_forecasting
            if ctx.enable_driver_forecasting is None
            else bool(ctx.enable_driver_forecasting)
        )
        selected_impl = registry.get(selected_model) if selected_model else None
        if (
            exog_enabled
            and selected_model
            and selected_impl is not None
            and selected_impl.capabilities.supports_exog
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
                    decision = _evaluate_plain_vs_exog(
                        registry,
                        model_name=selected_model,
                        values=effective_values,
                        dates=effective_dates,
                        exog_train=exog_bundle.exog_train,
                    )
                    use_exog = bool(decision and decision.get("exog_admitted"))
                    exog_spec = {
                        "mode": "forecast" if use_exog else "rejected",
                        "exog_admitted": use_exog,
                        "exog_rejected_reason": (
                            None if use_exog else (decision or {}).get("exog_rejected_reason") or "head_to_head_unavailable"
                        ),
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
                        "plain_vs_exog": decision,
                    }
                    mode_scores = _evaluate_arima_exog_modes(
                        registry,
                        line_item_id=li.id,
                        values=effective_values,
                        dates=effective_dates,
                        periods=[str(p) for p in periods[-len(effective_values):]],
                        version_id=ctx.version_id,
                        db=db,
                        cal_cfg=cal_cfg,
                        random_seed=random_seed,
                    )
                    exog_spec["exog_mode_scores"] = mode_scores or {
                        "n_folds": 0,
                        "mase_forecast": None,
                        "mase_known": None,
                    }
                    if not use_exog:
                        reason = exog_spec["exog_rejected_reason"]
                        out.warnings.append(
                            f"{li.name}: exog rejected ({reason})"
                        )
                else:
                    out.warnings.append(
                        f"{li.name}: exog not admitted (effective train {min_train} < {needed})"
                    )
                    exog_spec = {
                        "mode": "rejected",
                        "exog_admitted": False,
                        "exog_rejected_reason": "insufficient_train_points",
                        "min_train_points": min_train,
                        "required_points": needed,
                    }

        forecast_output = registry.fit_and_predict(
            selected_model,
            effective_values,
            effective_dates,
            horizon,
            random_seed=random_seed,
            exog=(exog_bundle.exog_train if use_exog and exog_bundle is not None else None),
            exog_future=(exog_bundle.exog_future if use_exog and exog_bundle is not None else None),
        )
        if exog_spec is not None:
            forecast_output.parameters = dict(forecast_output.parameters or {})
            forecast_output.parameters["exog_spec"] = exog_spec
            if out.comparison is not None:
                out.comparison["exog_mode_scores"] = exog_spec.get("exog_mode_scores")
                out.comparison["exog_admitted"] = exog_spec.get("exog_admitted")
        if heuristic_meta:
            forecast_output.parameters = dict(forecast_output.parameters or {})
            forecast_output.parameters["active_heuristics"] = heuristic_meta
            if heuristic_nudge is not None:
                forecast_output.parameters["heuristic_nudge"] = heuristic_nudge

        # Delta-method interval inflation for driver uncertainty (Phase 8.3)
        bounds_method = BOUNDS_METHOD_MODEL
        if (
            use_exog
            and exog_bundle is not None
            and forecast_output.lower_bound is not None
            and forecast_output.upper_bound is not None
        ):
            inflated = _inflate_exog_intervals(
                db,
                point=forecast_output.point_forecast,
                lower=forecast_output.lower_bound,
                upper=forecast_output.upper_bound,
                periods=list(forecast_output.periods),
                betas=(forecast_output.parameters or {}).get("exog_betas") or {},
                specs=exog_bundle.specs,
                version_id=ctx.version_id,
            )
            if inflated is not None:
                forecast_output.lower_bound = inflated["lower"]
                forecast_output.upper_bound = inflated["upper"]
                forecast_output.parameters = dict(forecast_output.parameters or {})
                forecast_output.parameters["exog_variance_inflated"] = True
                forecast_output.parameters["exog_interval_inflation"] = inflated.get("meta")
                bounds_method = "exog_variance_inflated"
                if exog_spec is not None:
                    exog_spec["exog_variance_inflated"] = True
                    forecast_output.parameters["exog_spec"] = exog_spec

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
                "bounds_method": bounds_method,
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
