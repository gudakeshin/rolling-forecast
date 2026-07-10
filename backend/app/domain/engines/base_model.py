"""Base interface for statistical forecast models."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from typing import Any
import numpy as np
import pandas as pd


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


class IForecastModel(ABC):
    """
    Interface for pluggable forecast models.
    
    Each model type (ARIMA, Prophet, ETS, Linear) implements this interface.
    Models are registered in the ModelRegistry and selected automatically
    based on walk-forward cross-validation performance.
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

    @abstractmethod
    def fit(self, series: pd.Series, dates: pd.DatetimeIndex) -> dict[str, Any]:
        """
        Fit the model to historical data.
        
        Args:
            series: Historical values (float), indexed sequentially
            dates: Corresponding date index
            
        Returns:
            Model parameters dict (serializable for storage)
        """
        ...

    @abstractmethod
    def predict(
        self,
        params: dict[str, Any],
        horizon: int,
        last_date: pd.Timestamp,
        confidence_level: float = 0.80,
    ) -> ForecastOutput:
        """
        Generate forecast from fitted parameters.
        
        Args:
            params: Parameters from fit()
            horizon: Number of periods to forecast
            last_date: Last date in the training data
            confidence_level: Confidence interval width (0.80 = P10/P90)
            
        Returns:
            ForecastOutput with point forecast and bounds
        """
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
        """True rolling-origin cross-validation.

        For each fold i in 1..n_folds, train on series[:-fold_horizon*i] (minimum
        min_data_points), predict the next fold_horizon periods, record MAPE.
        Returns mean_mape, fold_mapes, n_folds_used. Recency-weighted mean gives
        more weight to later folds.
        """
        fold_mapes: list[float] = []
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
                actuals = test.values
                predicted = forecast.point_forecast[: len(actuals)]
                mask = actuals != 0
                if mask.sum() == 0:
                    fold_mapes.append(float("inf"))
                else:
                    mape = float(
                        np.mean(np.abs((actuals[mask] - predicted[mask]) / actuals[mask])) * 100
                    )
                    fold_mapes.append(mape)
            except Exception:
                fold_mapes.append(float("inf"))

        finite = [m for m in fold_mapes if m != float("inf")]
        if not finite:
            return {"mean_mape": float("inf"), "fold_mapes": fold_mapes, "n_folds_used": 0}

        # Recency-weighted: later folds weigh more
        weights = list(range(1, len(finite) + 1))
        mean_mape = float(sum(m * w for m, w in zip(finite, weights)) / sum(weights))
        return {
            "mean_mape": mean_mape,
            "fold_mapes": fold_mapes,
            "n_folds_used": len(finite),
        }
