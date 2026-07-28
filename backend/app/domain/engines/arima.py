"""ARIMA/SARIMA forecast model — fit once, predict via filter (no re-optimize)."""

from __future__ import annotations

import logging
import warnings
from typing import Any

import numpy as np
import pandas as pd

from app.domain.engines.base_model import (
    ForecastOutput,
    IForecastModel,
    ModelCapabilities,
    make_period_labels,
    seasonal_period_length,
)

logger = logging.getLogger(__name__)


class ARIMAModel(IForecastModel):
    """ARIMA/SARIMA model using statsmodels SARIMAX."""

    @property
    def name(self) -> str:
        return "arima"

    @property
    def min_data_points(self) -> int:
        return 18

    @property
    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(
            supports_exog=True,
            complexity_rank=30,
            min_data_points=18,
            base_confidence=60.0,
            cost_class="moderate",
            display_label="ARIMA",
        )

    def fit(
        self,
        series: pd.Series,
        dates: pd.DatetimeIndex,
        *,
        exog: pd.DataFrame | np.ndarray | None = None,
    ) -> dict[str, Any]:
        from statsmodels.tsa.statespace.sarimax import SARIMAX

        values = series.values.astype(float)
        exog_arr = None
        exog_cols: list[str] | None = None
        if exog is not None:
            if isinstance(exog, pd.DataFrame):
                exog_cols = [str(c) for c in exog.columns]
                exog_arr = np.asarray(exog.values, dtype=float)
            else:
                exog_arr = np.asarray(exog, dtype=float)
            if exog_arr.ndim == 1:
                exog_arr = exog_arr.reshape(-1, 1)
            if len(exog_arr) != len(values):
                raise ValueError("exog length must match training series length")
        m = seasonal_period_length()
        has_seasonality = len(values) >= 2 * m

        best_aic = float("inf")
        best_order = None
        best_seasonal = None
        best_fitted = None

        orders = [(1, 1, 1), (1, 1, 0), (0, 1, 1), (2, 1, 1), (1, 0, 1)]
        seasonal_orders = (
            [(1, 1, 1, m), (0, 1, 1, m), (0, 0, 0, 0)] if has_seasonality else [(0, 0, 0, 0)]
        )

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            for order in orders:
                for seasonal in seasonal_orders:
                    try:
                        model = SARIMAX(
                            values,
                            exog=exog_arr,
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
                            best_fitted = fitted
                    except Exception:
                        continue

        if best_fitted is None:
            try:
                model = SARIMAX(values, exog=exog_arr, order=(1, 1, 0))
                best_fitted = model.fit(disp=False)
                best_order = (1, 1, 0)
                best_seasonal = (0, 0, 0, 0)
            except Exception as e:
                logger.warning("ARIMA fit failed completely: %s", e)
                return {
                    "_failed": True,
                    "error": str(e),
                    "n_points": len(values),
                    "_values": values.tolist(),
                }

        residuals = np.asarray(best_fitted.resid, dtype=float)
        mape = float(np.mean(np.abs(residuals[2:] / (values[2:] + 1e-10)))) * 100
        ss_res = float(np.sum(residuals[2:] ** 2))
        ss_tot = float(np.sum((values[2:] - np.mean(values[2:])) ** 2))
        r_squared = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0.0

        return {
            "order": list(best_order),
            "seasonal_order": list(best_seasonal),
            "aic": float(best_fitted.aic),
            "bic": float(best_fitted.bic),
            "has_seasonality": has_seasonality and best_seasonal[-1] > 0 and any(best_seasonal[:3]),
            "residual_std": float(np.std(residuals)),
            "in_sample_mape": mape,
            "r_squared": float(r_squared),
            "n_points": len(values),
            # Fitted coefficients — predict uses filter(), not fit()
            "_params": np.asarray(best_fitted.params, dtype=float).tolist(),
            "_param_names": list(best_fitted.params.index.astype(str))
            if hasattr(best_fitted.params, "index")
            else None,
            "_values": values.tolist(),
            "_exog_columns": exog_cols,
            "_exog_values": exog_arr.tolist() if exog_arr is not None else None,
        }

    def predict(
        self,
        params: dict[str, Any],
        horizon: int,
        last_date: pd.Timestamp,
        confidence_level: float = 0.80,
        *,
        exog_future: pd.DataFrame | np.ndarray | None = None,
    ) -> ForecastOutput:
        from statsmodels.tsa.statespace.sarimax import SARIMAX

        periods = make_period_labels(last_date, horizon)
        alpha = 1.0 - confidence_level
        exog_future_arr = None
        if exog_future is not None:
            if isinstance(exog_future, pd.DataFrame):
                exog_future_arr = np.asarray(exog_future.values, dtype=float)
            else:
                exog_future_arr = np.asarray(exog_future, dtype=float)
            if exog_future_arr.ndim == 1:
                exog_future_arr = exog_future_arr.reshape(-1, 1)

        if params.get("_failed") or "_params" not in params:
            values = np.asarray(params.get("_values", [0]), dtype=float)
            forecast = np.full(horizon, values[-1] if len(values) else 0.0)
            std = float(np.std(values)) if len(values) > 1 else 1.0
            lower = forecast - 1.28 * std
            upper = forecast + 1.28 * std
        else:
            values = np.asarray(params["_values"], dtype=float)
            order = tuple(params["order"])
            seasonal_order = tuple(params["seasonal_order"])
            param_vec = np.asarray(params["_params"], dtype=float)
            exog_cols = params.get("_exog_columns")
            has_exog = bool(exog_cols)
            if has_exog and exog_future_arr is None:
                raise ValueError("exog_future required for exogenous ARIMA predict")
            if has_exog and len(exog_future_arr) < horizon:
                raise ValueError("exog_future shorter than forecast horizon")

            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                model = SARIMAX(
                    values,
                    exog=(
                        np.asarray(params.get("_exog_values"), dtype=float)
                        if has_exog and params.get("_exog_values") is not None
                        else None
                    ),
                    order=order,
                    seasonal_order=seasonal_order,
                    enforce_stationarity=False,
                    enforce_invertibility=False,
                )
                # Apply stored coefficients — do NOT re-optimize
                fitted = model.filter(param_vec)
                pred = fitted.get_forecast(
                    steps=horizon,
                    alpha=alpha,
                    exog=(exog_future_arr[:horizon] if has_exog else None),
                )
                forecast = np.asarray(pred.predicted_mean, dtype=float)
                ci = pred.conf_int()
                if hasattr(ci, "iloc"):
                    lower = np.asarray(ci.iloc[:, 0], dtype=float)
                    upper = np.asarray(ci.iloc[:, 1], dtype=float)
                else:
                    ci_arr = np.asarray(ci, dtype=float)
                    lower, upper = ci_arr[:, 0], ci_arr[:, 1]

        from app.services.driver_forecast_cache import extract_exog_betas

        public_params = {k: v for k, v in params.items() if not str(k).startswith("_")}
        betas = extract_exog_betas(params)
        if betas:
            public_params["exog_betas"] = betas

        return ForecastOutput(
            point_forecast=forecast,
            lower_bound=lower,
            upper_bound=upper,
            periods=periods,
            model_type=self.name,
            parameters=public_params,
            fit_metrics={
                "in_sample_mape": params.get("in_sample_mape", params.get("mape", 0)),
                "r_squared": params.get("r_squared", 0),
                "aic": params.get("aic"),
                "bic": params.get("bic"),
            },
            diagnostics={
                "seasonality_detected": params.get("has_seasonality", False),
                "seasonality_period": (
                    params["seasonal_order"][-1]
                    if params.get("has_seasonality") and params.get("seasonal_order")
                    else None
                ),
                "order": params.get("order"),
                "seasonal_order": params.get("seasonal_order"),
            },
        )
