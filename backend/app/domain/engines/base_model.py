"""Base interface for statistical forecast models."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

import numpy as np
import pandas as pd


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


@dataclass
class ForecastOutput:
    """Output from a forecast model."""
    point_forecast: np.ndarray  # P50 values
    lower_bound: np.ndarray     # P10 values
    upper_bound: np.ndarray     # P90 values
    periods: list[str]          # Period labels (YYYY-MM or FY2026-P01)
    model_type: str
    parameters: dict[str, Any] = field(default_factory=dict)
    fit_metrics: dict[str, float] = field(default_factory=dict)  # MAPE, R², AIC, etc.
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
        fold_mapes: list[float] = []
        fold_smapes: list[float] = []
        fold_mases: list[float] = []
        fold_pinball_10: list[float] = []
        fold_pinball_90: list[float] = []
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

                # MAPE — only on non-zero actuals (legacy; asymmetric)
                mask = actuals != 0
                if mask.sum() == 0:
                    fold_mapes.append(float("inf"))
                else:
                    fold_mapes.append(
                        float(np.mean(np.abs((actuals[mask] - predicted[mask]) / actuals[mask])) * 100)
                    )

                fold_smapes.append(_smape(actuals, predicted))

                denom, method = mase_denominator(np.asarray(train.values, dtype=float), m)
                scale_methods.append(method)
                if method == "ineligible" or denom <= 0:
                    fold_mases.append(float("inf"))
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

        finite_mase = [v for v in fold_mases if v != float("inf")]
        coverage_80 = (
            float(coverage_hits / coverage_total) if coverage_total else None
        )
        # Prefer the most common successful scale method
        ok_methods = [s for s in scale_methods if s not in ("ineligible", "error")]
        scale_method = max(set(ok_methods), key=ok_methods.count) if ok_methods else (
            scale_methods[-1] if scale_methods else "ineligible"
        )

        return {
            "mean_mape": _weighted_mean(fold_mapes),
            "mean_smape": _weighted_mean(fold_smapes),
            "mean_mase": _weighted_mean(fold_mases),
            "mean_pinball_10": _weighted_mean(fold_pinball_10),
            "mean_pinball_90": _weighted_mean(fold_pinball_90),
            "mean_pinball": (
                (
                    lambda a, b: (a + b) / 2.0
                    if a != float("inf") and b != float("inf")
                    else float("inf")
                )(_weighted_mean(fold_pinball_10), _weighted_mean(fold_pinball_90))
            ),
            "coverage_80": coverage_80,
            "n": n_test_points,
            "fold_mapes": fold_mapes,
            "fold_mases": fold_mases,
            "n_folds_used": len(finite_mase) if finite_mase else len(
                [m for m in fold_mapes if m != float("inf")]
            ),
            "mase_scale_method": scale_method,
        }
