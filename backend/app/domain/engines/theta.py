"""Theta method via statsmodels ThetaModel."""

from __future__ import annotations

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


class ThetaForecastModel(IForecastModel):
    """Theta method (Assimakopoulos & Nikolopoulos) — strong at n=24–60."""

    @property
    def name(self) -> str:
        return "theta"

    @property
    def min_data_points(self) -> int:
        return 12

    @property
    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(
            complexity_rank=15,
            min_data_points=12,
            base_confidence=58.0,
            cost_class="cheap",
            display_label="Theta",
        )

    def fit(self, series: pd.Series, dates: pd.DatetimeIndex) -> dict[str, Any]:
        from statsmodels.tsa.forecasting.theta import ThetaModel

        y = series.astype(float)
        # Prefer DatetimeIndex for period-aware seasonality
        if not isinstance(y.index, pd.DatetimeIndex):
            y = pd.Series(y.values, index=dates[: len(y)])
        m = seasonal_period_length()
        use_seasonal = len(y) >= 2 * m
        model = ThetaModel(y, period=m if use_seasonal else None)
        res = model.fit()
        n = len(y)
        # ThetaModelResults has no fittedvalues — use sigma2 for residual scale
        sigma2 = float(getattr(res, "sigma2", 0.0) or 0.0)
        resid_std = float(np.sqrt(max(sigma2, 0.0)))
        # Rough in-sample signal: SES-like level vs series (for confidence UI only)
        values = np.asarray(y.values, dtype=float)
        level = float(values[0])
        fitted = np.empty(n)
        alpha = float(res.params.get("alpha", 0.5)) if hasattr(res.params, "get") else 0.5
        for i, v in enumerate(values):
            fitted[i] = level
            level = alpha * float(v) + (1.0 - alpha) * level
        resid = values - fitted
        mape = float(np.mean(np.abs(resid / (np.abs(values) + 1e-10)))) * 100
        ss_res = float(np.sum(resid ** 2))
        ss_tot = float(np.sum((values - np.mean(values)) ** 2))
        r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0

        return {
            "period": m if use_seasonal else None,
            "use_seasonal": use_seasonal,
            "residual_std": resid_std if resid_std > 0 else float(np.std(resid, ddof=1)) if n > 1 else 0.0,
            "in_sample_mape": mape,
            "r_squared": float(r2),
            "n_points": n,
            "theta_params": {str(k): float(v) for k, v in dict(res.params).items()},
            # Minimal endog for state: full series needed by ThetaModel API
            "_values": values.tolist(),
            "_index": [str(t) for t in y.index],
        }

    def predict(
        self,
        params: dict[str, Any],
        horizon: int,
        last_date: pd.Timestamp,
        confidence_level: float = 0.80,
    ) -> ForecastOutput:
        from statsmodels.tsa.forecasting.theta import ThetaModel

        values = np.asarray(params["_values"], dtype=float)
        idx = pd.DatetimeIndex(params.get("_index") or pd.date_range(end=last_date, periods=len(values), freq="MS"))
        y = pd.Series(values, index=idx)
        m = params.get("period") or seasonal_period_length()
        use_seasonal = bool(params.get("use_seasonal"))
        model = ThetaModel(y, period=int(m) if use_seasonal else None)
        res = model.fit()  # Theta fit is closed-form / cheap — not iterative MLE re-opt
        alpha = 1.0 - confidence_level
        forecast = np.asarray(res.forecast(horizon), dtype=float)
        try:
            pi = res.prediction_intervals(horizon, alpha=alpha)
            if hasattr(pi, "iloc"):
                lower = np.asarray(pi.iloc[:, 0], dtype=float)
                upper = np.asarray(pi.iloc[:, 1], dtype=float)
            else:
                arr = np.asarray(pi, dtype=float)
                lower, upper = arr[:, 0], arr[:, 1]
        except Exception:
            z = 1.28
            sd = float(params.get("residual_std") or 0.0)
            widths = z * sd * np.sqrt(np.arange(1, horizon + 1))
            lower, upper = forecast - widths, forecast + widths

        return ForecastOutput(
            point_forecast=forecast,
            lower_bound=lower,
            upper_bound=upper,
            periods=make_period_labels(last_date, horizon),
            model_type=self.name,
            parameters={k: v for k, v in params.items() if not str(k).startswith("_")},
            fit_metrics={
                "in_sample_mape": params.get("in_sample_mape", 0),
                "r_squared": params.get("r_squared", 0),
            },
            diagnostics={
                "seasonality_detected": use_seasonal,
                "seasonality_period": m if use_seasonal else None,
            },
        )
