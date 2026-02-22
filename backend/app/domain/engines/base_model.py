"""Base interface for statistical forecast models."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any
import numpy as np
import pandas as pd


@dataclass
class ForecastOutput:
    """Output from a forecast model."""
    point_forecast: np.ndarray  # P50 values
    lower_bound: np.ndarray     # P10 values
    upper_bound: np.ndarray     # P90 values
    periods: list[str]          # YYYY-MM period labels
    model_type: str
    parameters: dict[str, Any] = field(default_factory=dict)
    fit_metrics: dict[str, float] = field(default_factory=dict)  # MAPE, R², AIC, etc.
    diagnostics: dict[str, Any] = field(default_factory=dict)    # Seasonality, breaks, etc.


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
        """
        Evaluate model using walk-forward cross-validation.
        
        Returns MAPE on the holdout set.
        """
        if len(series) < self.min_data_points + test_size:
            return float("inf")

        train = series[:-test_size]
        test = series[-test_size:]
        train_dates = dates[:-test_size]
        test_dates = dates[-test_size:]

        try:
            params = self.fit(train, train_dates)
            forecast = self.predict(
                params, test_size, train_dates[-1]
            )
            # Calculate MAPE
            actuals = test.values
            predicted = forecast.point_forecast[:len(actuals)]
            mask = actuals != 0
            if mask.sum() == 0:
                return float("inf")
            mape = np.mean(np.abs((actuals[mask] - predicted[mask]) / actuals[mask])) * 100
            return float(mape)
        except Exception:
            return float("inf")
