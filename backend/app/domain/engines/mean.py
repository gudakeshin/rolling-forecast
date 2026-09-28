"""Mean / simple-average forecast — very sparse history (EC1)."""

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


class MeanModel(IForecastModel):
    """Constant mean forecast; ``model_type`` stays ``average`` for compatibility."""

    @property
    def name(self) -> str:
        return "average"

    @property
    def min_data_points(self) -> int:
        return 1

    @property
    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(
            complexity_rank=5,
            min_data_points=1,
            base_confidence=25.0,
            is_benchmark=False,
            cost_class="trivial",
            display_label="Simple average",
            auto_selectable=False,
        )

    def fit(self, series: pd.Series, dates: pd.DatetimeIndex) -> dict[str, Any]:
        y = series.values.astype(float)
        mean = float(np.mean(y)) if len(y) else 0.0
        std = float(np.std(y, ddof=1)) if len(y) > 1 else abs(mean) * 0.2
        return {"_mean": mean, "_std": std, "_n": len(y)}

    def predict(
        self,
        params: dict[str, Any],
        horizon: int,
        last_date: pd.Timestamp,
        confidence_level: float = 0.80,
    ) -> ForecastOutput:
        mean = float(params["_mean"])
        std = float(params.get("_std") or 0.0)
        # Match prior generate_baseline average branch: z≈1.28 for ~80%
        half = 1.28 * std
        point = np.full(horizon, mean, dtype=float)
        return ForecastOutput(
            point_forecast=point,
            lower_bound=point - half,
            upper_bound=point + half,
            periods=make_period_labels(last_date, horizon),
            model_type=self.name,
            parameters={"mean": mean, "std": std},
            fit_metrics={"in_sample_mape": 0.0, "r_squared": 0.0},
            diagnostics={"very_sparse": True},
        )
