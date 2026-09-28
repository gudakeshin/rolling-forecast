"""Zero-activity forecast model — all-zero history (EC2)."""

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


class ZeroModel(IForecastModel):
    """Emit zeros for every horizon period; no statistical fit."""

    @property
    def name(self) -> str:
        return "zero"

    @property
    def min_data_points(self) -> int:
        return 1

    @property
    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(
            complexity_rank=0,
            min_data_points=1,
            base_confidence=0.0,
            is_benchmark=False,
            cost_class="trivial",
            display_label="Zero (inactive)",
            auto_selectable=False,
        )

    def fit(self, series: pd.Series, dates: pd.DatetimeIndex) -> dict[str, Any]:
        return {"_n": len(series)}

    def predict(
        self,
        params: dict[str, Any],
        horizon: int,
        last_date: pd.Timestamp,
        confidence_level: float = 0.80,
    ) -> ForecastOutput:
        zeros = np.zeros(horizon, dtype=float)
        return ForecastOutput(
            point_forecast=zeros,
            lower_bound=zeros.copy(),
            upper_bound=zeros.copy(),
            periods=make_period_labels(last_date, horizon),
            model_type=self.name,
            parameters={},
            fit_metrics={"in_sample_mape": 0.0, "r_squared": 1.0},
            diagnostics={"all_zeros": True},
        )
