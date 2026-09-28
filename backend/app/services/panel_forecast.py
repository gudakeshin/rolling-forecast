"""DB-aware orchestration for the global panel model (Phase 2.1).

Plays the role for GlobalGBMModel that driver_exog.py plays for per-line
exog: this is where DB access, driver-column assembly, and the walk-forward
panel CV live. app.domain.engines.global_gbm stays pure model code with no
Session import — this module is the seam between the two.

Entry point: build_global_panel_context(), called once per version-generation
run (see generate_baseline.py), before the per-line loop.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sqlalchemy.orm import Session

from app.domain.engines.base_model import mase_denominator, seasonal_period_length
from app.domain.engines.global_gbm import (
    QUANTILES,
    GlobalPanelContext,
    fiscal_features,
    horizon_feature,
    lag_rolling_features,
)
from app.domain.engines.model_registry import ModelRegistry
from app.models.line_item import LineItem
from app.services.driver_exog import build_exog_for_line
from app.services.driver_forecast_cache import build_origin_overlays
from app.services.period_calendar import FiscalCalendarConfig

if TYPE_CHECKING:
    from app.services.forecast_pipeline import EffectiveSeriesResult

logger = logging.getLogger(__name__)

# Cross-sectional categorical grouping fields on LineItem, one-hot encoded.
# Low cardinality in an FP&A panel (dozens to low-hundreds of lines) — no
# leakage-prone target encoding needed at this scale.
CATEGORY_FIELDS: tuple[str, ...] = ("category", "business_unit", "product_line")
# Fixed positional driver-value slots. "driver_1" is not the same underlying
# driver across different lines — a deliberate v1 simplification that keeps
# the panel's feature schema bounded regardless of which lines have which
# specific active (human-promoted) driver links.
N_DRIVER_SLOTS = 3
N_FOLDS = 3
FOLD_HORIZON = 3
MIN_POINTS = 6


def _smape(actuals: np.ndarray, predicted: np.ndarray) -> float:
    denom = np.abs(actuals) + np.abs(predicted)
    mask = denom > 1e-12
    if mask.sum() == 0:
        return float("inf")
    return float(np.mean(2.0 * np.abs(actuals[mask] - predicted[mask]) / denom[mask]) * 100)


def _pinball(actuals: np.ndarray, quantile_forecast: np.ndarray, tau: float) -> float:
    err = actuals - quantile_forecast
    return float(np.mean(np.where(err >= 0, tau * err, (tau - 1.0) * err)))


def _weighted_mean(values: list[float]) -> float:
    finite = [v for v in values if v != float("inf") and not np.isnan(v)]
    if not finite:
        return float("inf")
    weights = list(range(1, len(finite) + 1))
    return float(sum(v * w for v, w in zip(finite, weights)) / sum(weights))


def _future_periods(last_period: str, horizon: int, cal_cfg: FiscalCalendarConfig | None) -> list[str]:
    from app.services.period_calendar import forecast_horizon_periods, get_calendar_config

    cfg = cal_cfg or get_calendar_config()
    return forecast_horizon_periods(last_period, horizon, cfg)


def _category_onehot(line_items: list[LineItem]) -> dict[int, dict[str, float]]:
    """One-hot vectors built from THIS panel's own category vocabulary."""
    vocab: dict[str, set[str]] = {f: set() for f in CATEGORY_FIELDS}
    for li in line_items:
        for f in CATEGORY_FIELDS:
            val = getattr(li, f, None)
            if val:
                vocab[f].add(str(val))
    onehot: dict[int, dict[str, float]] = {}
    for li in line_items:
        row: dict[str, float] = {}
        for f in CATEGORY_FIELDS:
            val = getattr(li, f, None)
            for v in sorted(vocab[f]):
                row[f"cat_{f}_{v}"] = 1.0 if val == v else 0.0
        onehot[li.id] = row
    return onehot


def _driver_feature_names() -> list[str]:
    return [f"driver_{i}_value" for i in range(1, N_DRIVER_SLOTS + 1)]


def _driver_row_from_series(exog_row: "pd.Series | None") -> dict[str, float]:
    names = _driver_feature_names()
    row: dict[str, float] = {n: float("nan") for n in names}
    if exog_row is None:
        return row
    values = list(exog_row.values)
    for i, v in enumerate(values[:N_DRIVER_SLOTS]):
        try:
            fv = float(v)
        except (TypeError, ValueError):
            fv = float("nan")
        row[names[i]] = fv if np.isfinite(fv) else float("nan")
    return row


def build_global_panel_context(
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
) -> GlobalPanelContext | None:
    """Build the global panel model for one version-generation run.

    Returns None when there isn't enough of a panel to justify a cross-series
    model at all. The caller (generate_baseline.py) wraps this whole call in
    try/except — any exception here must never fail a forecast run.
    """
    eligible = [
        li
        for li in line_items
        if (res := effective_series_by_line.get(li.id)) is not None
        and res.forced is None
        and len(res.values) >= MIN_POINTS
    ]
    if len(eligible) < 3:
        return None

    onehot = _category_onehot(eligible)

    # Full-history driver bundle per line: actual values across all observed
    # history + the true future horizon (already-staged driver forecasts, via
    # the same build_exog_for_line every other engine uses). No CV leakage
    # here — a production fit legitimately uses everything known "now".
    full_bundles: dict[int, Any] = {}
    for li in eligible:
        periods = periods_by_line[li.id]
        future_periods = _future_periods(periods[-1], horizon, cal_cfg)
        bundle = build_exog_for_line(
            db,
            line_item_id=li.id,
            train_periods=periods,
            future_periods=future_periods,
            version_id=version_id,
            max_links=N_DRIVER_SLOTS,
        )
        full_bundles[li.id] = (bundle, future_periods)

    training_rows: list[dict[str, float]] = []
    training_targets: list[float] = []
    predict_features_by_line: dict[int, pd.DataFrame] = {}

    for li in eligible:
        res = effective_series_by_line[li.id]
        values = np.asarray(res.values.values, dtype=float)
        periods = periods_by_line[li.id]
        bundle, future_periods = full_bundles[li.id]
        n = len(values)
        exog_train = bundle.exog_train if bundle is not None else None
        exog_future = bundle.exog_future if bundle is not None else None

        # Direct multi-horizon training rows: every (origin, h) window fully
        # inside observed history — one model handles every step of the
        # forecast instead of a separate booster per horizon.
        for t in range(n - 1):
            for h in range(1, horizon + 1):
                target_idx = t + h
                if target_idx >= n:
                    break
                feats = lag_rolling_features(values, t)
                feats.update(fiscal_features(periods[target_idx]))
                feats.update(horizon_feature(h))
                feats.update(onehot.get(li.id, {}))
                exog_row = (
                    exog_train.iloc[target_idx]
                    if exog_train is not None and target_idx < len(exog_train)
                    else None
                )
                feats.update(_driver_row_from_series(exog_row))
                training_rows.append(feats)
                training_targets.append(float(values[target_idx]))

        future_rows: list[dict[str, float]] = []
        for h in range(1, horizon + 1):
            feats = lag_rolling_features(values, n - 1)
            future_period = future_periods[h - 1] if h - 1 < len(future_periods) else periods[-1]
            feats.update(fiscal_features(future_period))
            feats.update(horizon_feature(h))
            feats.update(onehot.get(li.id, {}))
            exog_row = (
                exog_future.iloc[h - 1]
                if exog_future is not None and h - 1 < len(exog_future)
                else None
            )
            feats.update(_driver_row_from_series(exog_row))
            future_rows.append(feats)
        predict_features_by_line[li.id] = pd.DataFrame(future_rows)

    if not training_rows:
        return None

    train_df = pd.DataFrame(training_rows)
    feature_columns = list(train_df.columns)
    y = np.asarray(training_targets, dtype=float)

    production_models: dict[float, HistGradientBoostingRegressor] = {}
    for q in QUANTILES:
        gbm = HistGradientBoostingRegressor(
            loss="quantile", quantile=q, random_state=random_seed, max_iter=150
        )
        gbm.fit(train_df[feature_columns], y)
        production_models[q] = gbm

    per_line_cv_results = _panel_walk_forward_cv(
        db,
        eligible=eligible,
        effective_series_by_line=effective_series_by_line,
        periods_by_line=periods_by_line,
        onehot=onehot,
        full_bundles=full_bundles,
        feature_columns=feature_columns,
        cal_cfg=cal_cfg,
        random_seed=random_seed,
        registry=registry,
    )

    return GlobalPanelContext(
        feature_columns=feature_columns,
        production_models=production_models,
        predict_features_by_line=predict_features_by_line,
        per_line_cv_results=per_line_cv_results,
        fitted_at=datetime.now(timezone.utc).isoformat(),
        n_training_rows=len(training_rows),
        n_lines=len(eligible),
    )


def _fold_feature_frame(rows: list[dict[str, float]], feature_columns: list[str]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    for col in feature_columns:
        if col not in df.columns:
            df[col] = float("nan")
    return df[feature_columns]


def _panel_walk_forward_cv(
    db: Session,
    *,
    eligible: list[LineItem],
    effective_series_by_line: dict[int, "EffectiveSeriesResult"],
    periods_by_line: dict[int, list[str]],
    onehot: dict[int, dict[str, float]],
    full_bundles: dict[int, Any],
    feature_columns: list[str],
    cal_cfg: FiscalCalendarConfig | None,
    random_seed: int,
    registry: ModelRegistry,
) -> dict[int, dict[str, Any]]:
    """One shared walk-forward CV, computed once for the whole panel.

    For each of N_FOLDS folds: refit the global model ONCE on every line's
    data up to that line's own fold cutoff (fold windows are per-line, same
    as the rest of the registry's CV — no shared calendar cutoff needed),
    then score each line's own held-out window. Held-out driver columns use
    origin-restricted forecasts (build_origin_overlays), never actuals — the
    same leakage guard _evaluate_arima_exog_modes already applies to ARIMA.

    n_folds/fold_horizon are intentionally fixed (not read from the caller)
    — see GlobalGBMModel.evaluate_cv_panel's docstring for why.
    """
    m = seasonal_period_length()
    per_fold_train: list[tuple[list[dict[str, float]], list[float]]] = []
    per_fold_test: list[dict[int, tuple[list[dict[str, float]], list[float], float]]] = []

    for fold in range(1, N_FOLDS + 1):
        rows: list[dict[str, float]] = []
        targets: list[float] = []
        test_by_line: dict[int, tuple[list[dict[str, float]], list[float], float]] = {}

        for li in eligible:
            res = effective_series_by_line[li.id]
            values = np.asarray(res.values.values, dtype=float)
            periods = periods_by_line[li.id]
            n = len(values)
            holdout = FOLD_HORIZON * fold
            train_end = n - holdout
            if train_end < MIN_POINTS:
                continue
            test_start = train_end
            test_end = min(train_end + FOLD_HORIZON, n)
            if test_end <= test_start:
                continue

            bundle, _future = full_bundles[li.id]
            exog_train_full = bundle.exog_train if bundle is not None else None

            for t in range(train_end - 1):
                for h in range(1, FOLD_HORIZON + 1):
                    target_idx = t + h
                    if target_idx >= train_end:
                        break
                    feats = lag_rolling_features(values, t)
                    feats.update(fiscal_features(periods[target_idx]))
                    feats.update(horizon_feature(h))
                    feats.update(onehot.get(li.id, {}))
                    exog_row = (
                        exog_train_full.iloc[target_idx]
                        if exog_train_full is not None and target_idx < len(exog_train_full)
                        else None
                    )
                    feats.update(_driver_row_from_series(exog_row))
                    rows.append(feats)
                    targets.append(float(values[target_idx]))

            origin_period = periods[train_end - 1]
            holdout_periods = [str(p) for p in periods[test_start:test_end]]
            driver_ids = [s.driver_id for s in (bundle.specs if bundle is not None else [])]
            fold_exog_future = None
            if driver_ids:
                try:
                    overlays = build_origin_overlays(
                        db,
                        driver_ids=driver_ids,
                        train_end_period=origin_period,
                        future_periods=holdout_periods,
                        registry=registry,
                        cal_cfg=cal_cfg,
                        random_seed=random_seed,
                    )
                    fold_bundle = build_exog_for_line(
                        db,
                        line_item_id=li.id,
                        train_periods=[str(p) for p in periods[:train_end]],
                        future_periods=holdout_periods,
                        version_id=None,
                        max_links=N_DRIVER_SLOTS,
                        overlays=overlays,
                    )
                    fold_exog_future = fold_bundle.exog_future if fold_bundle is not None else None
                except Exception as e:
                    logger.debug(
                        "origin-restricted driver overlay failed for line %s fold %d: %s",
                        li.id, fold, e,
                    )

            test_feats: list[dict[str, float]] = []
            origin_idx = train_end - 1
            for i in range(test_end - test_start):
                h = i + 1
                feats = lag_rolling_features(values, origin_idx)
                feats.update(fiscal_features(periods[test_start + i]))
                feats.update(horizon_feature(h))
                feats.update(onehot.get(li.id, {}))
                exog_row = (
                    fold_exog_future.iloc[i]
                    if fold_exog_future is not None and i < len(fold_exog_future)
                    else None
                )
                feats.update(_driver_row_from_series(exog_row))
                test_feats.append(feats)

            test_targets = [float(v) for v in values[test_start:test_end]]
            denom, _ = mase_denominator(values[:train_end], m)
            test_by_line[li.id] = (test_feats, test_targets, denom)

        per_fold_train.append((rows, targets))
        per_fold_test.append(test_by_line)

    fold_mapes: dict[int, list[float]] = {li.id: [] for li in eligible}
    fold_mases: dict[int, list[float]] = {li.id: [] for li in eligible}
    fold_smapes: dict[int, list[float]] = {li.id: [] for li in eligible}
    fold_pb10: dict[int, list[float]] = {li.id: [] for li in eligible}
    fold_pb90: dict[int, list[float]] = {li.id: [] for li in eligible}
    residuals: dict[int, dict[int, list[float]]] = {li.id: {} for li in eligible}
    coverage_hits: dict[int, int] = {li.id: 0 for li in eligible}
    coverage_total: dict[int, int] = {li.id: 0 for li in eligible}
    n_test: dict[int, int] = {li.id: 0 for li in eligible}
    n_folds_used: dict[int, int] = {li.id: 0 for li in eligible}

    for fold_idx in range(N_FOLDS):
        rows, targets = per_fold_train[fold_idx]
        test_by_line = per_fold_test[fold_idx]
        if not rows or not test_by_line:
            continue

        fold_df = _fold_feature_frame(rows, feature_columns)
        y = np.asarray(targets, dtype=float)
        fold_models: dict[float, HistGradientBoostingRegressor] = {}
        try:
            for q in QUANTILES:
                gbm = HistGradientBoostingRegressor(
                    loss="quantile", quantile=q, random_state=random_seed, max_iter=150
                )
                gbm.fit(fold_df, y)
                fold_models[q] = gbm
        except Exception as e:
            logger.warning("global_gbm panel CV fold %d fit failed: %s", fold_idx + 1, e)
            continue

        for li_id, (test_feats, test_targets, denom) in test_by_line.items():
            if not test_feats:
                continue
            test_df = _fold_feature_frame(test_feats, feature_columns)
            try:
                preds = {q: np.asarray(fold_models[q].predict(test_df), dtype=float) for q in QUANTILES}
            except Exception:
                continue

            actuals = np.asarray(test_targets, dtype=float)
            stacked = np.vstack([preds[0.10], preds[0.50], preds[0.90]])
            stacked.sort(axis=0)
            lower, point, upper = stacked[0], stacked[1], stacked[2]

            for step, resid in enumerate(actuals - point, start=1):
                if np.isfinite(resid):
                    residuals[li_id].setdefault(step, []).append(float(resid))

            mask = actuals != 0
            if mask.sum() == 0:
                fold_mapes[li_id].append(float("inf"))
            else:
                fold_mapes[li_id].append(
                    float(np.mean(np.abs((actuals[mask] - point[mask]) / actuals[mask])) * 100)
                )
            fold_smapes[li_id].append(_smape(actuals, point))
            fold_mases[li_id].append(
                float(np.mean(np.abs(actuals - point)) / denom) if denom > 1e-12 else float("inf")
            )
            fold_pb10[li_id].append(_pinball(actuals, lower, 0.10))
            fold_pb90[li_id].append(_pinball(actuals, upper, 0.90))
            inside = (actuals >= lower) & (actuals <= upper)
            coverage_hits[li_id] += int(inside.sum())
            coverage_total[li_id] += len(actuals)
            n_test[li_id] += len(actuals)
            n_folds_used[li_id] += 1

    results: dict[int, dict[str, Any]] = {}
    for li in eligible:
        li_id = li.id
        if not fold_mapes[li_id]:
            continue  # never got a usable fold this run — leave out (ineligible)

        cov_total = coverage_total[li_id]
        mean_pb10 = _weighted_mean(fold_pb10[li_id])
        mean_pb90 = _weighted_mean(fold_pb90[li_id])
        mean_pb = (
            (mean_pb10 + mean_pb90) / 2.0
            if mean_pb10 != float("inf") and mean_pb90 != float("inf")
            else float("inf")
        )
        results[li_id] = {
            "mean_mape": _weighted_mean(fold_mapes[li_id]),
            "mean_smape": _weighted_mean(fold_smapes[li_id]),
            "mean_mase": _weighted_mean(fold_mases[li_id]),
            "mean_pinball_10": mean_pb10,
            "mean_pinball_90": mean_pb90,
            "mean_pinball": mean_pb,
            "coverage_80": float(coverage_hits[li_id] / cov_total) if cov_total else None,
            "fold_residuals": {h: v for h, v in sorted(residuals[li_id].items())},
            "n": n_test[li_id],
            "fold_mapes": fold_mapes[li_id],
            "fold_mases": fold_mases[li_id],
            "n_folds_used": n_folds_used[li_id],
            "mase_scale_method": "seasonal_naive",
        }
    return results
