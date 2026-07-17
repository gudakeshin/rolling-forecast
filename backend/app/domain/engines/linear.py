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
        X = np.arange(len(series), dtype=float)
        y = series.values.astype(float)

        model = LinearRegression()
        model.fit(X.reshape(-1, 1), y)

        predictions = model.predict(X.reshape(-1, 1))
        residuals = y - predictions
        n = len(series)
        # Unbiased residual std (ddof=2 for intercept+slope)
        dof = max(n - 2, 1)
        residual_std = float(np.sqrt(np.sum(residuals ** 2) / dof))

        ss_res = float(np.sum(residuals ** 2))
        ss_tot = float(np.sum((y - np.mean(y)) ** 2))
        r_squared = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0.0
        in_sample_mape = float(np.mean(np.abs(residuals / (np.abs(y) + 1e-10)))) * 100

        x_bar = float(np.mean(X))
        sxx = float(np.sum((X - x_bar) ** 2))

        return {
            "slope": float(model.coef_[0]),
            "intercept": float(model.intercept_),
            "residual_std": residual_std,
            "r_squared": float(r_squared),
            "in_sample_mape": in_sample_mape,
            "n_points": n,
            "x_bar": x_bar,
            "sxx": sxx,
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
        n_points = int(params["n_points"])
        x_bar = float(params.get("x_bar", (n_points - 1) / 2.0))
        sxx = float(params.get("sxx", 0.0))
        if sxx <= 0:
            sxx = float(np.sum((np.arange(n_points) - x_bar) ** 2)) + 1e-12

        future_indices = np.arange(n_points, n_points + horizon, dtype=float)
        point_forecast = slope * future_indices + intercept

        # OLS prediction interval: ŷ ± t·s·sqrt(1 + 1/n + (x−x̄)²/Sxx)
        alpha = 1.0 - confidence_level
        dof = max(n_points - 2, 1)
        try:
            from scipy import stats as scipy_stats

            t_crit = float(scipy_stats.t.ppf(1.0 - alpha / 2.0, dof))
        except Exception:
            # Fallback ~N(0,1) quantile for 80% → 1.28
            t_crit = 1.2815515655446004 if abs(confidence_level - 0.80) < 1e-6 else 1.96

        se = residual_std * np.sqrt(
            1.0 + 1.0 / n_points + (future_indices - x_bar) ** 2 / sxx
        )
        interval_widths = t_crit * se

        lower = point_forecast - interval_widths
        upper = point_forecast + interval_widths

        from app.domain.engines.base_model import make_period_labels

        periods = make_period_labels(last_date, horizon)

        return ForecastOutput(
            point_forecast=point_forecast,
            lower_bound=lower,
            upper_bound=upper,
            periods=periods,
            model_type="linear",
            parameters=params,
            fit_metrics={
                "r_squared": params["r_squared"],
                "in_sample_mape": params.get("in_sample_mape", 0.0),
            },
            diagnostics={"seasonality_detected": False, "pi_method": "ols_prediction"},
        )
