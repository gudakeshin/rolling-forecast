"""Base interface for statistical forecast models."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Literal, Protocol, cast

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    from app.domain.engines.global_gbm import GlobalPanelContext


CostClass = Literal["trivial", "cheap", "moderate", "expensive"]


@dataclass(frozen=True)
class ModelCapabilities:
    """Declarative metadata that replaces hardcoded model name lists."""

    supports_exog: bool = False
    handles_intermittent: bool = False
    complexity_rank: int = 50  # lower = simpler (tie-break)
    min_data_points: int = 12
    min_points_seasonal: int = 24
    max_folds_short_series: int | None = None  # cap folds when series is short
    short_series_threshold: int = 36
    base_confidence: float = 50.0
    is_benchmark: bool = False
    cost_class: CostClass = "cheap"
    display_label: str = ""
    auto_selectable: bool = True  # False for edge-case-only models (zero/average)
    # True only for the global cross-series panel model — signals that fit/
    # predict/CV must go through SupportsPanelModel + a GlobalPanelContext
    # rather than the plain per-series contract. See as_panel_model().
    is_panel_model: bool = False


@dataclass
class ForecastOutput:
    """Output from a forecast model."""
    point_forecast: np.ndarray  # P50 values
    lower_bound: np.ndarray     # P10 values
    upper_bound: np.ndarray     # P90 values
    periods: list[str]          # Period labels (YYYY-MM or FY2026-P01)
    model_type: str
    parameters: dict[str, Any] = field(default_factory=dict)
    # float | None: AIC/AICc are genuinely unavailable for some engines, and
    # 0.0 is a meaningful AIC — collapsing None to 0.0 would corrupt
    # model comparison. Consumers already treat missing as None.
    fit_metrics: dict[str, float | None] = field(default_factory=dict)
    diagnostics: dict[str, Any] = field(default_factory=dict)    # Seasonality, breaks, etc.


def make_period_labels(last_date: pd.Timestamp | date, horizon: int) -> list[str]:
    """Horizon period labels after last_date using the active fiscal calendar."""
    from app.services.period_calendar import date_to_period, forecast_horizon_periods, get_calendar_config

    cfg = get_calendar_config()
    if isinstance(last_date, pd.Timestamp):
        d = last_date.date()
    else:
        d = last_date
    return forecast_horizon_periods(date_to_period(d, cfg), horizon, cfg)


def seasonal_period_length() -> int:
    """Seasonal period from the active fiscal calendar — never hardcode 12 at call sites."""
    from app.services.period_calendar import get_calendar_config, periods_in_year

    return int(periods_in_year(get_calendar_config()))


def seasonal_naive_forecast(y: np.ndarray, horizon: int, m: int) -> np.ndarray:
    """Repeat the last ``m`` observations for ``horizon`` steps."""
    if m <= 0 or len(y) < m:
        last = float(y[-1]) if len(y) else 0.0
        return np.full(horizon, last, dtype=float)
    season = y[-m:]
    reps = int(np.ceil(horizon / m))
    return np.tile(season, reps)[:horizon].astype(float)


def mase_denominator(y_train: np.ndarray, m: int) -> tuple[float, str]:
    """MASE scale with documented fallback ladder.

    1. Seasonal-naive MAE on the training fold when ``n_train > 2m``
    2. Else lag-1 naive MAE
    3. Else ``mean(|y_train|)``
    4. Else ineligible (returns 0.0, ``"ineligible"``)
    """
    y = np.asarray(y_train, dtype=float)
    n = len(y)
    eps = 1e-12

    if n > 2 * m and m >= 1:
        # Seasonal-naive in-sample errors: y[t] - y[t-m]
        denom = float(np.mean(np.abs(y[m:] - y[:-m])))
        if denom > eps:
            return denom, "seasonal_naive"

    if n > 1:
        denom = float(np.mean(np.abs(np.diff(y))))
        if denom > eps:
            return denom, "naive"

    mean_abs = float(np.mean(np.abs(y))) if n else 0.0
    if mean_abs > eps:
        return mean_abs, "mean_abs"

    return 0.0, "ineligible"


def _smape(actuals: np.ndarray, predicted: np.ndarray) -> float:
    denom = np.abs(actuals) + np.abs(predicted)
    mask = denom > 1e-12
    if mask.sum() == 0:
        # Actuals and forecast both all-zero: a perfect forecast, not a failure.
        return 0.0
    return float(np.mean(2.0 * np.abs(actuals[mask] - predicted[mask]) / denom[mask]) * 100)


def _pinball(actuals: np.ndarray, quantile_forecast: np.ndarray, tau: float) -> float:
    err = actuals - quantile_forecast
    return float(np.mean(np.where(err >= 0, tau * err, (tau - 1.0) * err)))


def recency_weighted_mean(values: Sequence[float | None]) -> float:
    """Recency-weighted mean of per-fold CV scores, MOST RECENT fold first.

    ``values[0]`` is fold 1 — the latest origin (``holdout = fold_horizon *
    fold`` in evaluate_cv) — and gets weight k; the oldest fold gets weight 1.
    Weights are positional, so a skipped fold never shifts its neighbours.

    * ``None`` marks a fold the *data* made unscorable for this metric (all-zero
      actuals for MAPE, a flat training segment for MASE). That is identical
      for every model on the series, so the fold is skipped.
    * Any inf/nan is the *model* failing that fold (exception, non-finite
      forecast) and fails the whole model (returns inf). Scoring only the
      surviving folds would reward a model for blowing up on hard windows.
    """
    k = len(values)
    total = 0.0
    weight_sum = 0.0
    for i, v in enumerate(values):
        if v is None:
            continue
        if not np.isfinite(v):
            return float("inf")
        w = float(k - i)
        total += float(v) * w
        weight_sum += w
    if weight_sum == 0:
        return float("inf")
    return total / weight_sum


def cv_fold_horizon(forecast_horizon: int, n_points: int, min_train: int = 12) -> int:
    """Walk-forward CV fold length for a series, shared by every model.

    Folds score the horizon the forecast will actually be used at (default
    12 months) rather than a fixed 3-step window — a model that wins at h=1–3
    is not necessarily the one that wins at h=12. Folds are non-overlapping,
    so on short history the fold is shortened until a ``min_train``-point
    model still gets two folds (an SE from one fold is meaningless); never
    below 3 steps unless the forecast itself is shorter. Depends only on the
    series, never on the model, so every candidate is scored on the same
    windows and per-fold scores can be paired.
    """
    cap = max(3, (n_points - min_train) // 2)
    return max(1, min(int(forecast_horizon), cap))


class SupportsExog(Protocol):
    """The subset of IForecastModel that accepts exogenous regressors.

    Only models declaring ``capabilities.supports_exog`` implement these
    keyword arguments (today: ARIMA/SARIMAX). Keeping them off the base ABC
    means the other engines are not forced into a signature they cannot honour.
    """

    def fit(
        self,
        series: pd.Series,
        dates: pd.DatetimeIndex,
        *,
        exog: pd.DataFrame | np.ndarray | None = None,
    ) -> dict[str, Any]: ...

    def predict(
        self,
        params: dict[str, Any],
        horizon: int,
        last_date: pd.Timestamp,
        confidence_level: float = 0.80,
        *,
        exog_future: pd.DataFrame | np.ndarray | None = None,
    ) -> ForecastOutput: ...


def as_exog_model(model: IForecastModel) -> SupportsExog:
    """Narrow a registry model to its exog-capable interface.

    Raises when the model does not declare ``supports_exog``. Previously such a
    call raised TypeError deep inside a broad ``except Exception`` that recorded
    the fold as ``inf`` — so a misconfigured exog run looked like a model that
    simply scored badly. Call this outside the try block.
    """
    if not model.capabilities.supports_exog:
        raise TypeError(
            f"model {model.name!r} does not support exogenous regressors "
            "(capabilities.supports_exog is False)"
        )
    return cast(SupportsExog, model)


class SupportsPanelModel(Protocol):
    """The subset of IForecastModel a global cross-series model implements.

    Mirrors SupportsExog: only a model declaring ``capabilities.is_panel_model``
    implements these keyword arguments (today: GlobalGBMModel). A panel model
    cannot fit/predict/evaluate from ``(series, dates)`` alone — it needs the
    ``GlobalPanelContext`` built once per version-generation run (fitted
    boosters, precomputed per-line CV results and future feature rows) and the
    line item's own id to look itself up inside that shared panel.
    """

    def fit(
        self,
        series: pd.Series,
        dates: pd.DatetimeIndex,
        *,
        panel: "GlobalPanelContext | None" = None,
        line_item_id: int | None = None,
    ) -> dict[str, Any]: ...

    def predict(
        self,
        params: dict[str, Any],
        horizon: int,
        last_date: pd.Timestamp,
        confidence_level: float = 0.80,
        *,
        panel: "GlobalPanelContext | None" = None,
    ) -> ForecastOutput: ...

    def evaluate_cv_panel(
        self,
        line_item_id: int,
        series: pd.Series,
        dates: pd.DatetimeIndex,
        panel: "GlobalPanelContext",
        n_folds: int = 3,
        fold_horizon: int = 3,
    ) -> dict[str, Any]: ...


def as_panel_model(model: IForecastModel) -> SupportsPanelModel:
    """Narrow a registry model to its panel-model interface.

    Raises when the model does not declare ``is_panel_model`` — call this
    outside any broad ``except Exception`` block, same precedent as
    ``as_exog_model``.
    """
    if not model.capabilities.is_panel_model:
        raise TypeError(
            f"model {model.name!r} is not a panel model "
            "(capabilities.is_panel_model is False)"
        )
    return cast(SupportsPanelModel, model)


class IForecastModel(ABC):
    """
    Interface for pluggable forecast models.

    Each model type implements this interface. Models are registered in the
    ModelRegistry and selected automatically based on walk-forward CV.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Model type name (e.g., 'arima', 'prophet', 'ets', 'linear')."""
        ...

    @property
    def min_data_points(self) -> int:
        """Minimum number of historical data points required."""
        return 12

    @property
    def capabilities(self) -> ModelCapabilities:
        """Concrete default derived from ``min_data_points``; override per model."""
        return ModelCapabilities(
            min_data_points=self.min_data_points,
            display_label=self.name,
        )

    @abstractmethod
    def fit(self, series: pd.Series, dates: pd.DatetimeIndex) -> dict[str, Any]:
        """Fit the model to historical data; return serializable params."""
        ...

    @abstractmethod
    def predict(
        self,
        params: dict[str, Any],
        horizon: int,
        last_date: pd.Timestamp,
        confidence_level: float = 0.80,
    ) -> ForecastOutput:
        """Generate forecast from fitted parameters."""
        ...

    def evaluate(
        self, series: pd.Series, dates: pd.DatetimeIndex, test_size: int = 6
    ) -> float:
        """Single-holdout MAPE (legacy). Prefer evaluate_cv for model selection."""
        result = self.evaluate_cv(series, dates, n_folds=1, fold_horizon=test_size)
        return result["mean_mape"]

    def evaluate_cv(
        self,
        series: pd.Series,
        dates: pd.DatetimeIndex,
        n_folds: int = 3,
        fold_horizon: int = 3,
    ) -> dict[str, Any]:
        """Rolling-origin CV with multi-metric scoring.

        Returns mape/smape/mase/pinball/coverage plus fold lists. MASE uses the
        denominator ladder in ``mase_denominator`` (never emits inf from a flat
        training segment — those folds are marked ineligible).
        """
        # Fold lists run most-recent-first (fold 1 = latest origin). None = the
        # data made this fold unscorable for that metric; inf = model failed.
        fold_mapes: list[float | None] = []
        fold_smapes: list[float] = []
        fold_mases: list[float | None] = []
        fold_pinball_10: list[float] = []
        fold_pinball_90: list[float] = []
        # Out-of-sample residuals keyed by horizon step (1-based). These are the
        # calibration set for split-conformal intervals — see
        # app/services/interval_calibration.py. Previously discarded.
        residuals_by_h: dict[int, list[float]] = {}
        coverage_hits = 0
        coverage_total = 0
        scale_methods: list[str] = []
        n_test_points = 0
        m = seasonal_period_length()
        n = len(series)

        for fold in range(1, n_folds + 1):
            holdout = fold_horizon * fold
            train_end = n - holdout
            if train_end < self.min_data_points:
                break
            test_start = train_end
            test_end = min(train_end + fold_horizon, n)
            if test_end <= test_start:
                break
            train = series.iloc[:train_end]
            test = series.iloc[test_start:test_end]
            train_dates = dates[:train_end]
            try:
                params = self.fit(train, train_dates)
                forecast = self.predict(params, len(test), train_dates[-1])
                actuals = np.asarray(test.values, dtype=float)
                predicted = np.asarray(forecast.point_forecast[: len(actuals)], dtype=float)
                lower = np.asarray(forecast.lower_bound[: len(actuals)], dtype=float)
                upper = np.asarray(forecast.upper_bound[: len(actuals)], dtype=float)
                n_test_points += len(actuals)

                # Keep the raw signed errors, bucketed by how far ahead of the
                # fold origin they were predicted. h=1 errors are smaller than
                # h=12 errors, so pooling them would give flat, wrong bands.
                for step, resid in enumerate(actuals - predicted, start=1):
                    if np.isfinite(resid):
                        residuals_by_h.setdefault(step, []).append(float(resid))

                # MAPE — only on non-zero actuals (legacy; asymmetric)
                mask = actuals != 0
                if mask.sum() == 0:
                    fold_mapes.append(None)
                else:
                    fold_mapes.append(
                        float(np.mean(np.abs((actuals[mask] - predicted[mask]) / actuals[mask])) * 100)
                    )

                fold_smapes.append(_smape(actuals, predicted))

                denom, method = mase_denominator(np.asarray(train.values, dtype=float), m)
                scale_methods.append(method)
                if method == "ineligible" or denom <= 0:
                    fold_mases.append(None)
                else:
                    fold_mases.append(float(np.mean(np.abs(actuals - predicted)) / denom))

                fold_pinball_10.append(_pinball(actuals, lower, 0.10))
                fold_pinball_90.append(_pinball(actuals, upper, 0.90))

                inside = (actuals >= lower) & (actuals <= upper)
                coverage_hits += int(inside.sum())
                coverage_total += len(actuals)
            except Exception:
                fold_mapes.append(float("inf"))
                fold_smapes.append(float("inf"))
                fold_mases.append(float("inf"))
                fold_pinball_10.append(float("inf"))
                fold_pinball_90.append(float("inf"))
                scale_methods.append("error")

        def _scored(folds: list[float | None]) -> int:
            return sum(1 for v in folds if v is not None and np.isfinite(v))

        coverage_80 = (
            float(coverage_hits / coverage_total) if coverage_total else None
        )
        # Prefer the most common successful scale method
        ok_methods = [s for s in scale_methods if s not in ("ineligible", "error")]
        scale_method = max(set(ok_methods), key=ok_methods.count) if ok_methods else (
            scale_methods[-1] if scale_methods else "ineligible"
        )

        return {
            "mean_mape": recency_weighted_mean(fold_mapes),
            "mean_smape": recency_weighted_mean(fold_smapes),
            "mean_mase": recency_weighted_mean(fold_mases),
            "mean_pinball_10": recency_weighted_mean(fold_pinball_10),
            "mean_pinball_90": recency_weighted_mean(fold_pinball_90),
            "mean_pinball": (
                (
                    lambda a, b: (a + b) / 2.0
                    if a != float("inf") and b != float("inf")
                    else float("inf")
                )(recency_weighted_mean(fold_pinball_10), recency_weighted_mean(fold_pinball_90))
            ),
            "coverage_80": coverage_80,
            "fold_residuals": {h: list(v) for h, v in sorted(residuals_by_h.items())},
            "n": n_test_points,
            "fold_mapes": fold_mapes,
            "fold_mases": fold_mases,
            "fold_horizon": fold_horizon,
            "n_folds_used": _scored(fold_mases) or _scored(fold_mapes),
            "mase_scale_method": scale_method,
        }
