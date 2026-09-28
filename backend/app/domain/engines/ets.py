"""Exponential Smoothing (ETS) via statsmodels ETSModel — AICc variant selection."""

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


def _aicc(n: int, k: int, aic: float) -> float:
    """Small-sample corrected AIC. Falls back to AIC when n ≤ k+1."""
    if n <= k + 1:
        return float(aic)
    return float(aic + (2 * k * (k + 1)) / (n - k - 1))


class ETSModel(IForecastModel):
    """ETS with damped-trend candidates and real prediction intervals."""

    @property
    def name(self) -> str:
        return "ets"

    @property
    def min_data_points(self) -> int:
        return 12

    @property
    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(
            complexity_rank=20,
            min_data_points=12,
            base_confidence=55.0,
            cost_class="cheap",
            display_label="ETS",
        )

    def _candidate_specs(self, n: int, m: int, positive: bool) -> list[dict[str, Any]]:
        specs: list[dict[str, Any]] = []
        trends: tuple[str | None, ...] = (None, "add")
        has_season = n >= 2 * m
        # Multiplicative error/seasonal need strictly positive data throughout.
        if has_season:
            seasonals: tuple[str | None, ...] = (None, "add", "mul") if positive else (None, "add")
        else:
            seasonals = (None,)
        errors: tuple[str, ...] = ("add", "mul") if positive else ("add",)

        for err in errors:
            for trend in trends:
                for seasonal in seasonals:
                    damp_opts = (False, True) if trend is not None else (False,)
                    for damped in damp_opts:
                        specs.append({
                            "error": err,
                            "trend": trend,
                            "seasonal": seasonal,
                            "damped_trend": damped,
                            "seasonal_periods": m if seasonal else None,
                            "transform": "none",
                        })

        if positive:
            # Box-Cox (log, lambda=0) alternative: additive components on the log
            # scale approximate multiplicative dynamics without the optimizer
            # instability native mul-error/mul-seasonal can hit on short series.
            log_seasonals: tuple[str | None, ...] = (None, "add") if has_season else (None,)
            for trend in trends:
                for seasonal in log_seasonals:
                    damp_opts = (False, True) if trend is not None else (False,)
                    for damped in damp_opts:
                        specs.append({
                            "error": "add",
                            "trend": trend,
                            "seasonal": seasonal,
                            "damped_trend": damped,
                            "seasonal_periods": m if seasonal else None,
                            "transform": "log",
                        })
        return specs

    def fit(self, series: pd.Series, dates: pd.DatetimeIndex) -> dict[str, Any]:
        from statsmodels.tsa.exponential_smoothing.ets import ETSModel as SMETSModel

        y = series.astype(float)
        if not isinstance(y.index, pd.DatetimeIndex):
            y = pd.Series(np.asarray(y.values, dtype=float), index=dates[: len(y)])
        values = np.asarray(y.values, dtype=float)
        n = len(values)
        m = seasonal_period_length()
        positive = bool(n > 0 and np.all(values > 0))
        # Jacobian of z=log(y): comparing AIC across the log and raw scales
        # requires -2*log|dz/dy| = +2*sum(log(y)) added back to the log-scale AIC.
        log_jacobian = 2.0 * float(np.sum(np.log(values))) if positive else 0.0

        best: dict[str, Any] | None = None
        best_aicc = float("inf")
        best_params_vec: np.ndarray | None = None

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            for spec in self._candidate_specs(n, m, positive):
                try:
                    is_log = spec["transform"] == "log"
                    fit_series = pd.Series(np.log(values), index=y.index) if is_log else y
                    kwargs = {
                        "error": spec["error"],
                        "trend": spec["trend"],
                        "damped_trend": bool(spec["damped_trend"]) if spec["trend"] else False,
                        "seasonal": spec["seasonal"],
                        "initialization_method": "estimated",
                    }
                    if spec["seasonal"]:
                        kwargs["seasonal_periods"] = int(spec["seasonal_periods"])
                    model = SMETSModel(fit_series, **kwargs)
                    fitted = model.fit(disp=False)
                    aic = float(fitted.aic)
                    # Prefer native aicc when present
                    aicc = float(getattr(fitted, "aicc", _aicc(n, len(fitted.params), aic)))
                    if is_log:
                        aic += log_jacobian
                        aicc += log_jacobian
                    if aicc < best_aicc:
                        best_aicc = aicc
                        fitted_in_sample = np.asarray(fitted.fittedvalues, dtype=float)
                        if is_log:
                            resid_log = np.asarray(fitted.resid, dtype=float)
                            resid = values - np.exp(fitted_in_sample)
                            residual_std_log = float(np.std(resid_log, ddof=1)) if n > 1 else 0.0
                        else:
                            resid = np.asarray(fitted.resid, dtype=float)
                            residual_std_log = 0.0
                        mape = float(np.mean(np.abs(resid / (np.abs(values) + 1e-10)))) * 100
                        ss_res = float(np.sum(resid ** 2))
                        ss_tot = float(np.sum((values - np.mean(values)) ** 2))
                        r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
                        best_params_vec = np.asarray(fitted.params, dtype=float)
                        best = {
                            "error": spec["error"],
                            "trend": spec["trend"],
                            "seasonal": spec["seasonal"],
                            "damped_trend": bool(spec["damped_trend"]) if spec["trend"] else False,
                            "seasonal_periods": spec["seasonal_periods"],
                            "has_seasonality": spec["seasonal"] is not None,
                            "transform": spec["transform"],
                            "aic": aic,
                            "aicc": aicc,
                            "residual_std": float(np.std(resid, ddof=1)) if n > 1 else 0.0,
                            "residual_std_log": residual_std_log,
                            "in_sample_mape": mape,
                            "r_squared": float(r2),
                            "n_points": n,
                            "n_params": int(len(fitted.params)),
                            "_params": best_params_vec.tolist(),
                            "_values": values.tolist(),
                            "_index": [str(t) for t in y.index],
                        }
                except Exception:
                    continue

        if best is None:
            # Simple SES fallback — store level only (no re-opt path)
            alpha = 0.3
            level = float(values[0])
            fitted_vals = [level]
            for v in values[1:]:
                level = alpha * float(v) + (1 - alpha) * level
                fitted_vals.append(level)
            resid = values - np.asarray(fitted_vals)
            return {
                "error": "add",
                "trend": None,
                "seasonal": None,
                "damped_trend": False,
                "has_seasonality": False,
                "transform": "none",
                "smoothing_level": alpha,
                "last_level": float(level),
                "residual_std": float(np.std(resid)),
                "in_sample_mape": float(np.mean(np.abs(resid / (np.abs(values) + 1e-10)))) * 100,
                "r_squared": 0.0,
                "n_points": n,
                "_fallback": True,
                "_values": values.tolist(),
            }

        return best

    def predict(
        self,
        params: dict[str, Any],
        horizon: int,
        last_date: pd.Timestamp,
        confidence_level: float = 0.80,
    ) -> ForecastOutput:
        from statsmodels.tsa.exponential_smoothing.ets import ETSModel as SMETSModel

        values = np.asarray(params.get("_values", []), dtype=float)
        periods = make_period_labels(last_date, horizon)
        alpha_ci = 1.0 - confidence_level

        if params.get("_fallback") or "_params" not in params:
            level = float(params.get("last_level", values[-1] if len(values) else 0.0))
            forecast = np.full(horizon, level, dtype=float)
            sd = float(params.get("residual_std") or 0.0)
            widths = 1.28 * sd * np.sqrt(np.arange(1, horizon + 1))
            return ForecastOutput(
                point_forecast=forecast,
                lower_bound=forecast - widths,
                upper_bound=forecast + widths,
                periods=periods,
                model_type=self.name,
                parameters={k: v for k, v in params.items() if not str(k).startswith("_")},
                fit_metrics={
                    "in_sample_mape": params.get("in_sample_mape", 0),
                    "r_squared": params.get("r_squared", 0),
                    "aic": params.get("aic"),
                    "aicc": params.get("aicc"),
                },
                diagnostics={"seasonality_detected": False, "fallback": True},
            )

        idx = pd.DatetimeIndex(
            params.get("_index")
            or pd.date_range(end=last_date, periods=len(values), freq="MS")
        )
        transform = params.get("transform", "none")
        is_log = transform == "log"
        y = pd.Series(np.log(values) if is_log else values, index=idx)
        kwargs: dict[str, Any] = {
            "error": params.get("error") or "add",
            "trend": params.get("trend"),
            "damped_trend": bool(params.get("damped_trend")) if params.get("trend") else False,
            "seasonal": params.get("seasonal"),
            "initialization_method": "estimated",
        }
        if params.get("seasonal") and params.get("seasonal_periods"):
            kwargs["seasonal_periods"] = int(params["seasonal_periods"])

        model = SMETSModel(y, **kwargs)
        fitted = model.smooth(np.asarray(params["_params"], dtype=float))
        start = len(y)
        end = start + horizon - 1
        pred = fitted.get_prediction(start=start, end=end)
        raw_forecast = np.asarray(pred.predicted_mean, dtype=float)
        try:
            ci = pred.pred_int(alpha=alpha_ci)
            arr = np.asarray(ci, dtype=float)
            raw_lower, raw_upper = arr[:, 0], arr[:, 1]
        except Exception:
            sd = float((params.get("residual_std_log") if is_log else params.get("residual_std")) or 0.0)
            widths = 1.28 * sd * np.sqrt(np.arange(1, horizon + 1))
            raw_lower, raw_upper = raw_forecast - widths, raw_forecast + widths

        if is_log:
            forecast, lower, upper = np.exp(raw_forecast), np.exp(raw_lower), np.exp(raw_upper)
        else:
            forecast, lower, upper = raw_forecast, raw_lower, raw_upper

        return ForecastOutput(
            point_forecast=forecast,
            lower_bound=lower,
            upper_bound=upper,
            periods=periods,
            model_type=self.name,
            parameters={k: v for k, v in params.items() if not str(k).startswith("_")},
            fit_metrics={
                "in_sample_mape": params.get("in_sample_mape", 0),
                "r_squared": params.get("r_squared", 0),
                "aic": params.get("aic"),
                "aicc": params.get("aicc"),
            },
            diagnostics={
                "seasonality_detected": bool(params.get("has_seasonality")),
                "seasonality_period": params.get("seasonal_periods"),
                "damped_trend": bool(params.get("damped_trend")),
                "error": params.get("error"),
                "trend": params.get("trend"),
                "seasonal": params.get("seasonal"),
                "transform": transform,
            },
        )
