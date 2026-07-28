"""Per-fold-origin driver forecasts for honest Phase 8 CV (exog_mode=forecast).

Caches forecasts keyed by ``(driver_id, train_end_period, horizon)`` so folds that
share an origin across models do not re-fit the driver.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
from sqlalchemy.orm import Session

from app.domain.engines.model_registry import ModelRegistry
from app.services.driver_series import materialize_driver_series
from app.services.period_calendar import FiscalCalendarConfig, period_to_date

# Process-local cache — cleared between baseline runs via ``clear_driver_forecast_cache``.
_CACHE: dict[tuple[int, str, int], pd.Series] = {}


def clear_driver_forecast_cache() -> None:
    _CACHE.clear()


def forecast_driver_at_origin(
    db: Session,
    *,
    driver_id: int,
    train_end_period: str,
    future_periods: list[str],
    registry: ModelRegistry,
    cal_cfg: FiscalCalendarConfig | None,
    random_seed: int = 42,
) -> pd.Series:
    """Forecast a driver beyond ``train_end_period`` using only history up to that origin.

    Returns a period-indexed series covering ``future_periods`` (may be shorter if
    the model fails — callers should fall back conservatively).
    """
    horizon = len(future_periods)
    if horizon <= 0:
        return pd.Series(dtype=float)

    cache_key = (int(driver_id), str(train_end_period), int(horizon))
    if cache_key in _CACHE:
        cached = _CACHE[cache_key]
        return cached.reindex(pd.Index(future_periods, dtype=str))

    actual = materialize_driver_series(db, driver_id=driver_id, value_type="actual")
    if actual.empty:
        empty = pd.Series(dtype=float)
        _CACHE[cache_key] = empty
        return empty

    # Restrict to periods at or before the fold origin
    hist = actual[actual.index.astype(str) <= str(train_end_period)].astype(float)
    if len(hist) < 12:
        empty = pd.Series(dtype=float)
        _CACHE[cache_key] = empty
        return empty

    periods = [str(p) for p in hist.index]
    values = pd.Series(hist.values.astype(float))
    if cal_cfg is not None:
        dates = pd.DatetimeIndex([pd.Timestamp(period_to_date(p, cal_cfg)) for p in periods])
    else:
        dates = pd.DatetimeIndex([pd.Timestamp(f"{p}-01") for p in periods])

    candidate_models = [
        n
        for n in registry.list_models_by_complexity()
        if (
            (m := registry.get(n))
            and m.capabilities.auto_selectable
            and not m.capabilities.supports_exog
        )
    ]
    try:
        best, _, _ = registry.auto_select(
            values,
            dates,
            random_seed=random_seed,
            models_to_test=candidate_models or None,
            two_stage=True,
            is_material=False,
        )
        if not best:
            empty = pd.Series(dtype=float)
            _CACHE[cache_key] = empty
            return empty
        out = registry.fit_and_predict(
            best, values, dates, horizon, random_seed=random_seed
        )
        forecast = pd.Series(
            {str(p): float(v) for p, v in zip(out.periods, out.point_forecast)},
            dtype=float,
        )
    except Exception:
        forecast = pd.Series(dtype=float)

    _CACHE[cache_key] = forecast
    return forecast.reindex(pd.Index(future_periods, dtype=str))


def build_origin_overlays(
    db: Session,
    *,
    driver_ids: list[int],
    train_end_period: str,
    future_periods: list[str],
    registry: ModelRegistry,
    cal_cfg: FiscalCalendarConfig | None,
    random_seed: int = 42,
) -> dict[int, pd.Series]:
    """Map driver_id → origin-restricted forecast series for fold holdouts."""
    overlays: dict[int, pd.Series] = {}
    for did in driver_ids:
        s = forecast_driver_at_origin(
            db,
            driver_id=did,
            train_end_period=train_end_period,
            future_periods=future_periods,
            registry=registry,
            cal_cfg=cal_cfg,
            random_seed=random_seed,
        )
        if not s.empty and s.notna().any():
            overlays[int(did)] = s
    return overlays


def extract_exog_betas(fit_params: dict[str, Any]) -> dict[str, float]:
    """Map exog column name → coefficient from ARIMA/SARIMAX fit params."""
    cols = fit_params.get("_exog_columns") or []
    names = fit_params.get("_param_names") or []
    vals = fit_params.get("_params") or []
    if not cols or not names or not vals:
        return {}
    name_to_val = {str(n): float(v) for n, v in zip(names, vals)}
    out: dict[str, float] = {}
    for col in cols:
        key = str(col)
        if key in name_to_val:
            out[key] = name_to_val[key]
            continue
        # statsmodels sometimes prefixes with "x" / "beta."
        for n, v in name_to_val.items():
            if key in n or n.endswith(key):
                out[key] = float(v)
                break
    return out
