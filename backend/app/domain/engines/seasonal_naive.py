"""Seasonal-naive forecast — selectable benchmark for FVA / MASE floor."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from app.domain.engines.base_model import (
    ForecastOutput,
    IForecastModel,
    ModelCapabilities,
    make_period_labels,
    seasonal_naive_forecast,
    seasonal_period_length,
)


class SeasonalNaiveModel(IForecastModel):
    """Repeat the last seasonal cycle; period from the fiscal calendar."""

    @property
    def name(self) -> str:
        return "seasonal_naive"

    @property
    def min_data_points(self) -> int:
        return seasonal_period_length() + 1

    @property
    def capabilities(self) -> ModelCapabilities:
        m = seasonal_period_length()
        return ModelCapabilities(
            complexity_rank=1,
            min_data_points=m + 1,
            min_points_seasonal=2 * m,
            base_confidence=35.0,
            is_benchmark=True,
            cost_class="trivial",
            display_label="Seasonal naive",
        )

    def fit(self, series: pd.Series, dates: pd.DatetimeIndex) -> dict[str, Any]:
        y = series.values.astype(float)
        m = seasonal_period_length()
        if len(y) < m:
            raise ValueError(f"seasonal_naive needs ≥{m} points, have {len(y)}")
        season = y[-m:].astype(float)
        if len(y) > m:
            resid = y[m:] - y[:-m]
            residual_std = (
                float(np.std(resid, ddof=1))
                if len(resid) > 1
                else float(np.mean(np.abs(resid)))
            )
        else:
            residual_std = (
                float(np.std(season, ddof=1))
                if len(season) > 1
                else abs(float(season[-1])) * 0.1
            )
        return {
            "_season": season.tolist(),
            "_m": m,
            "_residual_std": residual_std,
            "_n": len(y),
        }

    def predict(
        self,
        params: dict[str, Any],
        horizon: int,
        last_date: pd.Timestamp,
        confidence_level: float = 0.80,
    ) -> ForecastOutput:
        season = np.asarray(params["_season"], dtype=float)
        m = int(params.get("_m") or len(season) or seasonal_period_length())
        sd = float(params.get("_residual_std") or 0.0)
        from scipy import stats

        z = float(stats.norm.ppf(0.5 + confidence_level / 2.0))
        point = seasonal_naive_forecast(season, horizon, m)
        cycle = np.arange(1, horizon + 1, dtype=float)
        half = z * sd * np.sqrt(np.ceil(cycle / max(m, 1)))
        periods = make_period_labels(last_date, horizon)
        return ForecastOutput(
            point_forecast=point,
            lower_bound=point - half,
            upper_bound=point + half,
            periods=periods,
            model_type=self.name,
            parameters={"seasonal_period": m},
            fit_metrics={"in_sample_mape": 0.0},
            diagnostics={"benchmark": True, "seasonality_period": m},
        )
