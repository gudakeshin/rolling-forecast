"""ARIMA/SARIMA forecast model."""

import numpy as np
import pandas as pd
from typing import Any
import logging

from app.domain.engines.base_model import IForecastModel, ForecastOutput

logger = logging.getLogger(__name__)


class ARIMAModel(IForecastModel):
    """ARIMA/SARIMA model using statsmodels."""

    @property
    def name(self) -> str:
        return "arima"

    @property
    def min_data_points(self) -> int:
        return 18  # Needs more data for differencing

    def fit(self, series: pd.Series, dates: pd.DatetimeIndex) -> dict[str, Any]:
        from statsmodels.tsa.statespace.sarimax import SARIMAX
        import warnings

        values = series.values.astype(float)
        has_seasonality = len(values) >= 24

        best_aic = float("inf")
        best_params = None
        best_order = None
        best_seasonal = None

        # Grid search over common ARIMA orders
        orders = [(1, 1, 1), (1, 1, 0), (0, 1, 1), (2, 1, 1), (1, 0, 1)]
        seasonal_orders = [(1, 1, 1, 12), (0, 1, 1, 12)] if has_seasonality else [(0, 0, 0, 0)]

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")

            for order in orders:
                for seasonal in seasonal_orders:
                    try:
                        model = SARIMAX(
                            values,
                            order=order,
                            seasonal_order=seasonal,
                            enforce_stationarity=False,
                            enforce_invertibility=False,
                        )
                        fitted = model.fit(disp=False, maxiter=100)
                        if fitted.aic < best_aic:
                            best_aic = fitted.aic
                            best_order = order
                            best_seasonal = seasonal
                            best_params = {
                                "order": list(order),
                                "seasonal_order": list(seasonal),
                                "aic": float(fitted.aic),
                                "bic": float(fitted.bic),
                            }
                    except Exception:
                        continue

        if best_params is None:
            # Fallback to simplest ARIMA
            try:
                model = SARIMAX(values, order=(1, 1, 0))
                fitted = model.fit(disp=False)
                best_order = (1, 1, 0)
                best_seasonal = (0, 0, 0, 0)
                best_params = {
                    "order": [1, 1, 0],
                    "seasonal_order": [0, 0, 0, 0],
                    "aic": float(fitted.aic),
                    "bic": float(fitted.bic),
                }
            except Exception as e:
                logger.warning(f"ARIMA fit failed completely: {e}")
                return {
                    "_failed": True,
                    "error": str(e),
                    "n_points": len(values),
                    "_values": values.tolist(),
                }

        # Refit best model for predictions
        model = SARIMAX(
            values,
            order=tuple(best_order),
            seasonal_order=tuple(best_seasonal),
            enforce_stationarity=False,
            enforce_invertibility=False,
        )
        fitted = model.fit(disp=False, maxiter=200)

        residuals = fitted.resid
        mape = float(np.mean(np.abs(residuals[2:] / (values[2:] + 1e-10)))) * 100
        ss_res = np.sum(residuals[2:] ** 2)
        ss_tot = np.sum((values[2:] - np.mean(values[2:])) ** 2)
        r_squared = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0

        best_params.update({
            "has_seasonality": has_seasonality and best_seasonal[0] > 0,
            "residual_std": float(np.std(residuals)),
            "in_sample_mape": mape,
            "r_squared": float(r_squared),
            "n_points": len(values),
            "_values": values.tolist(),
        })

        return best_params

    def predict(
        self,
        params: dict[str, Any],
        horizon: int,
        last_date: pd.Timestamp,
        confidence_level: float = 0.80,
    ) -> ForecastOutput:
        from statsmodels.tsa.statespace.sarimax import SARIMAX
        import warnings

        if params.get("_failed"):
            values = np.array(params.get("_values", [0]))
            forecast = np.full(horizon, values[-1])
            std = np.std(values) if len(values) > 1 else 1.0
            lower = forecast - 1.28 * std
            upper = forecast + 1.28 * std
        else:
            values = np.array(params["_values"])
            order = tuple(params["order"])
            seasonal_order = tuple(params["seasonal_order"])

            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                model = SARIMAX(
                    values,
                    order=order,
                    seasonal_order=seasonal_order,
                    enforce_stationarity=False,
                    enforce_invertibility=False,
                )
                fitted = model.fit(disp=False, maxiter=200)
                pred = fitted.get_forecast(steps=horizon, alpha=0.20)  # 80% CI
                forecast = pred.predicted_mean
                ci = pred.conf_int()
                lower = ci.iloc[:, 0].values
                upper = ci.iloc[:, 1].values

        from app.domain.engines.base_model import make_period_labels

        periods = make_period_labels(last_date, horizon)

        return ForecastOutput(
            point_forecast=forecast,
            lower_bound=lower,
            upper_bound=upper,
            periods=periods,
            model_type="arima",
            parameters={k: v for k, v in params.items() if not k.startswith("_")},
            fit_metrics={
                "in_sample_mape": params.get("in_sample_mape", params.get("mape", 0)),
                "r_squared": params.get("r_squared", 0),
                "aic": params.get("aic"),
                "bic": params.get("bic"),
            },
            diagnostics={
                "seasonality_detected": params.get("has_seasonality", False),
                "seasonality_period": 12 if params.get("has_seasonality") else None,
                "order": params.get("order"),
                "seasonal_order": params.get("seasonal_order"),
            },
        )
