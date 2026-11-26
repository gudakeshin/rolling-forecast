"""Once-per-run Chronos-2 batch: every line, every CV origin, one HTTP call.

The DB-aware seam for ``app.domain.engines.chronos2``, playing the role
``panel_forecast`` plays for the global GBM. Called from
``panel_forecast.build_global_panel_context`` when the running company's
registry contains ``chronos2`` (``enable_foundation_models``).

For each eligible line it emits one task per CV origin (the same windows
``ModelRegistry._evaluate_one`` scores per-series models on, so folds pair)
plus one production task from the last actual. Covariates:

* past covariates — the line's driver history up to the origin
  (``build_exog_for_line``);
* known-future covariates — for the production task, the version's staged
  driver paths; for a CV origin, driver forecasts made *from that origin
  only* (``build_origin_overlays``), never realised actuals, the same leakage
  guard the GBM panel CV and ARIMA exog admission use.

Group ids are ``<category>@<origin period>``: only lines whose contexts end at
the same period attend to one another, so a later context can never leak into
an earlier origin's forecast.

Never raises: any failure yields ``FoundationPanelContext.unavailable(reason)``
and the model is skipped on every line with that reason.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from app.domain.engines.base_model import (
    FoldPrediction,
    cv_fold_horizon,
    cv_fold_windows,
    cv_n_folds,
    seasonal_period_length,
    summarize_cv_folds,
)
from app.domain.engines.chronos2 import (
    CHRONOS2_MIN_POINTS,
    CHRONOS2_NAME,
    FoundationPanelContext,
    QuantileForecast,
)
from app.domain.engines.model_registry import ModelRegistry
from app.models.line_item import LineItem
from app.services.period_calendar import FiscalCalendarConfig

if TYPE_CHECKING:
    from app.services.forecast_pipeline import EffectiveSeriesResult

logger = logging.getLogger(__name__)

N_DRIVER_LINKS = 3


def _task_id(line_item_id: int, origin_period: str) -> str:
    return f"{line_item_id}@{origin_period}"


def _finite_columns(
    past: pd.DataFrame | None, future: pd.DataFrame | None, n_past: int, n_future: int
) -> tuple[dict[str, list[float]], dict[str, list[float]]]:
    """Covariates as JSON lists, keeping only fully-finite, correctly-sized columns.

    A column with a gap (driver history starting later than the line, a
    failed overlay) is dropped rather than imputed: imputing would invent
    driver values. A future column is kept only alongside its past column
    (Chronos-2 requires known-future covariates to have history too).
    """
    past_cov: dict[str, list[float]] = {}
    future_cov: dict[str, list[float]] = {}
    if past is None or len(past) < n_past:
        return past_cov, future_cov
    for col in past.columns:
        p = np.asarray(past[col].iloc[-n_past:], dtype=float) if n_past else np.array([])
        if len(p) != n_past or not np.all(np.isfinite(p)):
            continue
        name = str(col)
        past_cov[name] = [float(v) for v in p]
        if future is not None and col in future.columns and len(future) >= n_future:
            f = np.asarray(future[col].iloc[:n_future], dtype=float)
            if len(f) == n_future and np.all(np.isfinite(f)):
                future_cov[name] = [float(v) for v in f]
    return past_cov, future_cov


def _future_periods(last_period: str, horizon: int, cal_cfg: FiscalCalendarConfig | None) -> list[str]:
    from app.services.period_calendar import forecast_horizon_periods, get_calendar_config

    return forecast_horizon_periods(last_period, horizon, cal_cfg or get_calendar_config())


def _origin_future_exog(
    db: Session,
    *,
    line_item_id: int,
    driver_ids: list[int],
    train_periods: list[str],
    holdout_periods: list[str],
    registry: ModelRegistry,
    cal_cfg: FiscalCalendarConfig | None,
    random_seed: int,
) -> pd.DataFrame | None:
    """Known-future driver values as forecast from a CV origin (no actuals)."""
    if not driver_ids:
        return None
    from app.services.driver_exog import build_exog_for_line
    from app.services.driver_forecast_cache import build_origin_overlays

    try:
        overlays = build_origin_overlays(
            db,
            driver_ids=driver_ids,
            train_end_period=train_periods[-1],
            future_periods=holdout_periods,
            registry=registry,
            cal_cfg=cal_cfg,
            random_seed=random_seed,
        )
        bundle = build_exog_for_line(
            db,
            line_item_id=line_item_id,
            train_periods=train_periods,
            future_periods=holdout_periods,
            version_id=None,
            max_links=N_DRIVER_LINKS,
            overlays=overlays,
        )
    except Exception as e:  # a missing overlay drops the covariate, never the line
        logger.debug("chronos2 origin overlay failed for line %s: %s", line_item_id, e)
        return None
    return bundle.exog_future if bundle is not None else None


def build_foundation_batch(
    db: Session,
    *,
    line_items: list[LineItem],
    effective_series_by_line: dict[int, "EffectiveSeriesResult"],
    periods_by_line: dict[int, list[str]],
    cal_cfg: FiscalCalendarConfig | None,
    version_id: str,
    horizon: int,
    random_seed: int,
    registry: ModelRegistry,
    include_covariates: bool = True,
) -> tuple[list[dict[str, Any]], dict[int, dict[str, Any]]]:
    """The request series, plus per-line bookkeeping to score the CV folds."""
    from app.services.driver_exog import build_exog_for_line

    series: list[dict[str, Any]] = []
    plan: dict[int, dict[str, Any]] = {}
    for li in line_items:
        res = effective_series_by_line.get(li.id)
        if res is None or res.forced is not None:
            continue
        values = np.asarray(res.values.values, dtype=float)
        n = len(values)
        if n < CHRONOS2_MIN_POINTS or not np.all(np.isfinite(values)):
            continue
        # Effective values can be a suffix of the raw history (structural-break
        # truncation): align periods to the values actually forecast.
        periods = [str(p) for p in periods_by_line.get(li.id, [])][-n:]
        if len(periods) != n:
            continue
        group = str(getattr(li, "category", None) or "_")
        future_periods = _future_periods(periods[-1], horizon, cal_cfg)

        bundle = None
        if include_covariates:
            try:
                bundle = build_exog_for_line(
                    db,
                    line_item_id=li.id,
                    train_periods=periods,
                    future_periods=future_periods,
                    version_id=version_id,
                    max_links=N_DRIVER_LINKS,
                )
            except Exception as e:
                logger.debug("chronos2 driver bundle failed for line %s: %s", li.id, e)
        exog_train = bundle.exog_train if bundle is not None else None
        driver_ids = [s.driver_id for s in bundle.specs] if bundle is not None else []

        past_cov, future_cov = _finite_columns(
            exog_train, bundle.exog_future if bundle is not None else None, n, horizon
        )
        series.append({
            "id": _task_id(li.id, periods[-1]),
            "target": [float(v) for v in values],
            "prediction_length": int(horizon),
            "past_covariates": past_cov,
            "future_covariates": future_cov,
            "group_id": f"{group}@{periods[-1]}",
        })

        fold_horizon = cv_fold_horizon(horizon, n)
        n_folds = cv_n_folds(n, min_data_points=CHRONOS2_MIN_POINTS, fold_horizon=fold_horizon)
        windows = cv_fold_windows(
            n, min_data_points=CHRONOS2_MIN_POINTS, n_folds=n_folds, fold_horizon=fold_horizon
        )
        for train_end, test_end in windows:
            origin = periods[train_end - 1]
            fold_future = (
                _origin_future_exog(
                    db,
                    line_item_id=li.id,
                    driver_ids=driver_ids,
                    train_periods=periods[:train_end],
                    holdout_periods=periods[train_end:test_end],
                    registry=registry,
                    cal_cfg=cal_cfg,
                    random_seed=random_seed,
                )
                if driver_ids
                else None
            )
            fold_past = exog_train.iloc[:train_end] if exog_train is not None else None
            p_cov, f_cov = _finite_columns(fold_past, fold_future, train_end, test_end - train_end)
            series.append({
                "id": _task_id(li.id, origin),
                "target": [float(v) for v in values[:train_end]],
                "prediction_length": int(test_end - train_end),
                "past_covariates": p_cov,
                "future_covariates": f_cov,
                "group_id": f"{group}@{origin}",
            })
        plan[li.id] = {
            "values": values,
            "periods": periods,
            "windows": windows,
            "fold_horizon": fold_horizon,
        }
    return series, plan


def score_line_cv(
    line_item_id: int,
    line_plan: dict[str, Any],
    forecasts: dict[tuple[int, str], QuantileForecast],
) -> dict[str, Any] | None:
    values: np.ndarray = line_plan["values"]
    periods: list[str] = line_plan["periods"]
    folds: list[FoldPrediction | None] = []
    for train_end, test_end in line_plan["windows"]:
        fc = forecasts.get((line_item_id, periods[train_end - 1]))
        k = test_end - train_end
        if fc is None or len(fc.p50) < k:
            folds.append(None)
            continue
        folds.append(FoldPrediction(
            train=values[:train_end],
            actuals=values[train_end:test_end],
            point=np.asarray(fc.p50[:k], dtype=float),
            lower=np.asarray(fc.p10[:k], dtype=float),
            upper=np.asarray(fc.p90[:k], dtype=float),
        ))
    if not folds:
        return None
    return summarize_cv_folds(folds, fold_horizon=line_plan["fold_horizon"], m=seasonal_period_length())


def _record_provenance(db: Session, version_id: str, fm: FoundationPanelContext) -> None:
    """Store provenance on the version, next to input_hash (committed by the caller)."""
    try:
        from app.models.forecast import ForecastVersion

        version = db.get(ForecastVersion, version_id)
        if version is not None:
            version.foundation_model_provenance = {
                "model": CHRONOS2_NAME,
                "status": fm.status,
                "skip_reason": fm.skip_reason,
                **fm.provenance,
            }
    except Exception as e:  # provenance is best-effort; never fail the run
        logger.warning("could not record foundation provenance: %s", e)


def build_foundation_context(
    db: Session,
    *,
    line_items: list[LineItem],
    effective_series_by_line: dict[int, "EffectiveSeriesResult"],
    periods_by_line: dict[int, list[str]],
    cal_cfg: FiscalCalendarConfig | None,
    version_id: str,
    horizon: int,
    random_seed: int,
    registry: ModelRegistry,
) -> FoundationPanelContext:
    """Build the run's Chronos-2 context with exactly one service call."""
    from app.services.foundation_client import FoundationServiceUnavailable, forecast_batch

    fitted_at = datetime.now(timezone.utc).isoformat()
    try:
        series, plan = build_foundation_batch(
            db,
            line_items=line_items,
            effective_series_by_line=effective_series_by_line,
            periods_by_line=periods_by_line,
            cal_cfg=cal_cfg,
            version_id=version_id,
            horizon=horizon,
            random_seed=random_seed,
            registry=registry,
        )
        if not series:
            fm = FoundationPanelContext.unavailable("no line has enough history for Chronos-2")
        else:
            result = forecast_batch(series)
            forecasts: dict[tuple[int, str], QuantileForecast] = {}
            for sid, path in result.forecasts.items():
                li_str, origin = sid.split("@", 1)
                forecasts[(int(li_str), origin)] = QuantileForecast(
                    p10=np.asarray(path.p10, dtype=float),
                    p50=np.asarray(path.p50, dtype=float),
                    p90=np.asarray(path.p90, dtype=float),
                )
            per_line_cv: dict[int, dict[str, Any]] = {}
            for li_id, line_plan in plan.items():
                cv = score_line_cv(li_id, line_plan, forecasts)
                if cv is not None:
                    per_line_cv[li_id] = cv
            fm = FoundationPanelContext(
                status="ok",
                provenance={
                    **result.provenance,
                    "fitted_at": fitted_at,
                    "n_series": len(series),
                    "n_lines": len(plan),
                },
                forecasts=forecasts,
                production_origin_by_line={li_id: p["periods"][-1] for li_id, p in plan.items()},
                per_line_cv_results=per_line_cv,
            )
    except FoundationServiceUnavailable as e:
        logger.warning("Chronos-2 skipped this run: %s", e)
        fm = FoundationPanelContext.unavailable(str(e), {"fitted_at": fitted_at})
    except Exception as e:
        logger.warning("Chronos-2 batch build failed — skipping the model: %s", e, exc_info=True)
        fm = FoundationPanelContext.unavailable(
            f"batch build failed: {type(e).__name__}: {e}", {"fitted_at": fitted_at}
        )
    _record_provenance(db, version_id, fm)
    return fm
