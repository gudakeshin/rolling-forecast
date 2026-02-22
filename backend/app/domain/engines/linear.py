"""Linear trend forecast model -- simplest baseline model."""

import numpy as np
import pandas as pd
from typing import Any
from sklearn.linear_model import LinearRegression

from app.domain.engines.base_model import IForecastModel, ForecastOutput


class LinearTrendModel(IForecastModel):
    """Simple linear trend model. Used as fallback for short or sparse data."""

    @property
    def name(self) -> str:
        return "linear"

    @property
    def min_data_points(self) -> int:
        return 6  # Needs very little data

    def fit(self, series: pd.Series, dates: pd.DatetimeIndex) -> dict[str, Any]:
        X = np.arange(len(series)).reshape(-1, 1)
        y = series.values

        model = LinearRegression()
        model.fit(X, y)

        # Compute residual std for confidence intervals
        predictions = model.predict(X)
        residuals = y - predictions
        residual_std = float(np.std(residuals))

        # R-squared
        ss_res = np.sum(residuals ** 2)
        ss_tot = np.sum((y - np.mean(y)) ** 2)
        r_squared = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0

        return {
            "slope": float(model.coef_[0]),
            "intercept": float(model.intercept_),
            "residual_std": residual_std,
            "r_squared": float(r_squared),
            "n_points": len(series),
        }

    def predict(
        self,
        params: dict[str, Any],
        horizon: int,
        last_date: pd.Timestamp,
        confidence_level: float = 0.80,
    ) -> ForecastOutput:
        slope = params["slope"]
        intercept = params["intercept"]
        residual_std = params["residual_std"]
        n_points = params["n_points"]

        # Generate future indices
        future_indices = np.arange(n_points, n_points + horizon)
        point_forecast = slope * future_indices + intercept

        # Confidence intervals (widen with distance from training data)
        z = 1.28  # ~P10/P90 for 80% CI
        interval_widths = z * residual_std * np.sqrt(1 + (future_indices - n_points / 2) ** 2 / (n_points * np.var(np.arange(n_points)) + 1e-10))
        # Simplified: use constant width
        interval_widths = z * residual_std * (1 + 0.1 * np.arange(horizon))

        lower = point_forecast - interval_widths
        upper = point_forecast + interval_widths

        # Generate period labels
        periods = []
        current = last_date
        for _ in range(horizon):
            current = current + pd.offsets.MonthBegin(1)
            periods.append(current.strftime("%Y-%m"))

        return ForecastOutput(
            point_forecast=point_forecast,
            lower_bound=lower,
            upper_bound=upper,
            periods=periods,
            model_type="linear",
            parameters=params,
            fit_metrics={"r_squared": params["r_squared"]},
            diagnostics={"seasonality_detected": False},
        )
