"""Exponential Smoothing (ETS) forecast model."""

import numpy as np
import pandas as pd
from typing import Any

from app.domain.engines.base_model import IForecastModel, ForecastOutput


class ETSModel(IForecastModel):
    """Exponential Smoothing model using statsmodels."""

    @property
    def name(self) -> str:
        return "ets"

    @property
    def min_data_points(self) -> int:
        return 12

    def fit(self, series: pd.Series, dates: pd.DatetimeIndex) -> dict[str, Any]:
        from statsmodels.tsa.holtwinters import ExponentialSmoothing

        values = series.values.astype(float)

        # Determine if seasonal (need at least 2 full cycles)
        has_seasonality = len(values) >= 24

        try:
            if has_seasonality:
                model = ExponentialSmoothing(
                    values,
                    trend="add",
                    seasonal="add",
                    seasonal_periods=12,
                    initialization_method="estimated",
                )
            else:
                model = ExponentialSmoothing(
                    values,
                    trend="add",
                    seasonal=None,
                    initialization_method="estimated",
                )

            fitted = model.fit(optimized=True)

            # Compute fit metrics
            residuals = fitted.resid
            mape = float(np.mean(np.abs(residuals / (values + 1e-10)))) * 100
            ss_res = np.sum(residuals ** 2)
            ss_tot = np.sum((values - np.mean(values)) ** 2)
            r_squared = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0

            return {
                "smoothing_level": float(fitted.params.get("smoothing_level", 0)),
                "smoothing_trend": float(fitted.params.get("smoothing_trend", 0)),
                "smoothing_seasonal": float(fitted.params.get("smoothing_seasonal", 0)),
                "has_seasonality": has_seasonality,
                "seasonal_periods": 12 if has_seasonality else None,
                "fitted_values": fitted.fittedvalues.tolist(),
                "residual_std": float(np.std(residuals)),
                "in_sample_mape": mape,
                "r_squared": float(r_squared),
                "aic": float(fitted.aic) if hasattr(fitted, "aic") else None,
                "n_points": len(values),
                # Store model params for re-creation
                "_params": {k: float(v) if isinstance(v, (np.floating, float)) else v
                           for k, v in fitted.params.items()},
                "_values": values.tolist(),
            }

        except Exception:
            # Fallback: simple exponential smoothing
            alpha = 0.3
            level = values[0]
            fitted_vals = [level]
            for v in values[1:]:
                level = alpha * v + (1 - alpha) * level
                fitted_vals.append(level)

            residuals = values - np.array(fitted_vals)
            return {
                "smoothing_level": alpha,
                "has_seasonality": False,
                "residual_std": float(np.std(residuals)),
                "last_level": float(level),
                "in_sample_mape": float(np.mean(np.abs(residuals / (values + 1e-10)))) * 100,
                "r_squared": 0.0,
                "n_points": len(values),
                "_values": values.tolist(),
                "_fallback": True,
            }

    def predict(
        self,
        params: dict[str, Any],
        horizon: int,
        last_date: pd.Timestamp,
        confidence_level: float = 0.80,
    ) -> ForecastOutput:
        from statsmodels.tsa.holtwinters import ExponentialSmoothing

        values = np.array(params.get("_values", []))
        residual_std = params.get("residual_std", 1.0)
        has_seasonality = params.get("has_seasonality", False)

        try:
            if not params.get("_fallback"):
                if has_seasonality:
                    model = ExponentialSmoothing(
                        values,
                        trend="add",
                        seasonal="add",
                        seasonal_periods=12,
                        initialization_method="estimated",
                    )
                else:
                    model = ExponentialSmoothing(
                        values,
                        trend="add",
                        seasonal=None,
                        initialization_method="estimated",
                    )
                fitted = model.fit(optimized=True)
                forecast = fitted.forecast(horizon)
            else:
                # Fallback: constant forecast at last level
                forecast = np.full(horizon, params.get("last_level", values[-1]))

        except Exception:
            forecast = np.full(horizon, values[-1] if len(values) > 0 else 0)

        # Confidence intervals
        z = 1.28
        widths = z * residual_std * np.sqrt(np.arange(1, horizon + 1))
        lower = forecast - widths
        upper = forecast + widths

        from app.domain.engines.base_model import make_period_labels

        periods = make_period_labels(last_date, horizon)

        return ForecastOutput(
            point_forecast=forecast,
            lower_bound=lower,
            upper_bound=upper,
            periods=periods,
            model_type="ets",
            parameters={k: v for k, v in params.items() if not k.startswith("_")},
            fit_metrics={
                "in_sample_mape": params.get("in_sample_mape", params.get("mape", 0)),
                "r_squared": params.get("r_squared", 0),
                "aic": params.get("aic"),
            },
            diagnostics={
                "seasonality_detected": has_seasonality,
                "seasonality_period": 12 if has_seasonality else None,
            },
        )
