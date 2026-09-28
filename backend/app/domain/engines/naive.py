"""Naive (last-value) forecast — selectable benchmark for FVA."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from app.domain.engines.base_model import (
    ForecastOutput,
    IForecastModel,
    ModelCapabilities,
    make_period_labels,
)


class NaiveModel(IForecastModel):
    """Repeat the last observation; intervals from lag-1 residual scale."""

    @property
    def name(self) -> str:
        return "naive"

    @property
    def min_data_points(self) -> int:
        return 1

    @property
    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(
            complexity_rank=0,
            min_data_points=1,
            base_confidence=30.0,
            is_benchmark=True,
            cost_class="trivial",
            display_label="Naive (last value)",
        )

    def fit(self, series: pd.Series, dates: pd.DatetimeIndex) -> dict[str, Any]:
        y = series.values.astype(float)
        last = float(y[-1])
        if len(y) > 1:
            resid = np.diff(y)
            residual_std = (
                float(np.std(resid, ddof=1)) if len(resid) > 1 else float(np.abs(resid[0]))
            )
        else:
            residual_std = abs(last) * 0.1 if last else 1.0
        return {
            "_last": last,
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
        last = float(params["_last"])
        sd = float(params.get("_residual_std") or 0.0)
        from scipy import stats

        z = float(stats.norm.ppf(0.5 + confidence_level / 2.0))
        point = np.full(horizon, last, dtype=float)
        steps = np.sqrt(np.arange(1, horizon + 1, dtype=float))
        half = z * sd * steps
        periods = make_period_labels(last_date, horizon)
        return ForecastOutput(
            point_forecast=point,
            lower_bound=point - half,
            upper_bound=point + half,
            periods=periods,
            model_type=self.name,
            parameters={k: v for k, v in params.items() if not str(k).startswith("_")},
            fit_metrics={"in_sample_mape": 0.0},
            diagnostics={"benchmark": True},
        )
