"""Prophet forecast model -- good for seasonal data with trend changes."""

import numpy as np
import pandas as pd
from typing import Any
import logging

from app.domain.engines.base_model import IForecastModel, ForecastOutput

logger = logging.getLogger(__name__)


class ProphetModel(IForecastModel):
    """Facebook Prophet model -- handles seasonality and changepoints well."""

    @property
    def name(self) -> str:
        return "prophet"

    @property
    def min_data_points(self) -> int:
        return 18

    def fit(self, series: pd.Series, dates: pd.DatetimeIndex) -> dict[str, Any]:
        from prophet import Prophet
        import logging as _logging

        # Suppress Prophet's verbose logging
        _logging.getLogger("prophet").setLevel(_logging.WARNING)
        _logging.getLogger("cmdstanpy").setLevel(_logging.WARNING)

        # Prepare data in Prophet format
        df = pd.DataFrame({
            "ds": dates,
            "y": series.values.astype(float),
        })

        try:
            model = Prophet(
                yearly_seasonality=True if len(series) >= 24 else False,
                weekly_seasonality=False,
                daily_seasonality=False,
                changepoint_prior_scale=0.05,
                seasonality_prior_scale=10,
                interval_width=0.80,
            )
            model.fit(df)

            # In-sample predictions for fit metrics
            in_sample = model.predict(df)
            actuals = series.values
            predicted = in_sample["yhat"].values

            residuals = actuals - predicted
            mask = actuals != 0
            mape = float(np.mean(np.abs(residuals[mask] / actuals[mask]))) * 100 if mask.sum() > 0 else 100.0
            ss_res = np.sum(residuals ** 2)
            ss_tot = np.sum((actuals - np.mean(actuals)) ** 2)
            r_squared = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0

            # Detect changepoints
            changepoints = model.changepoints
            has_changepoints = len(changepoints) > 0

            return {
                "yearly_seasonality": len(series) >= 24,
                "changepoint_prior_scale": 0.05,
                "n_changepoints": len(changepoints),
                "has_changepoints": has_changepoints,
                "residual_std": float(np.std(residuals)),
                "in_sample_mape": mape,
                "r_squared": float(r_squared),
                "n_points": len(series),
                "_df": df.to_dict(orient="records"),
            }

        except Exception as e:
            logger.warning(f"Prophet fit failed: {e}")
            return {
                "_failed": True,
                "error": str(e),
                "n_points": len(series),
                "_values": series.values.tolist(),
            }

    def predict(
        self,
        params: dict[str, Any],
        horizon: int,
        last_date: pd.Timestamp,
        confidence_level: float = 0.80,
    ) -> ForecastOutput:
        from prophet import Prophet
        import logging as _logging

        _logging.getLogger("prophet").setLevel(_logging.WARNING)
        _logging.getLogger("cmdstanpy").setLevel(_logging.WARNING)

        if params.get("_failed"):
            values = np.array(params.get("_values", [0]))
            forecast = np.full(horizon, values[-1])
            std = np.std(values) if len(values) > 1 else 1.0
            lower = forecast - 1.28 * std
            upper = forecast + 1.28 * std
        else:
            # Re-fit Prophet (it doesn't serialize well)
            df = pd.DataFrame(params["_df"])
            df["ds"] = pd.to_datetime(df["ds"])

            model = Prophet(
                yearly_seasonality=params.get("yearly_seasonality", True),
                weekly_seasonality=False,
                daily_seasonality=False,
                changepoint_prior_scale=params.get("changepoint_prior_scale", 0.05),
                interval_width=0.80,
            )
            model.fit(df)

            # Create future dataframe
            future = model.make_future_dataframe(periods=horizon, freq="MS")
            pred = model.predict(future)

            # Extract forecast periods only
            forecast_rows = pred.tail(horizon)
            forecast = forecast_rows["yhat"].values
            lower = forecast_rows["yhat_lower"].values
            upper = forecast_rows["yhat_upper"].values

        # Period labels
        periods = []
        current = last_date
        for _ in range(horizon):
            current = current + pd.offsets.MonthBegin(1)
            periods.append(current.strftime("%Y-%m"))

        return ForecastOutput(
            point_forecast=forecast,
            lower_bound=lower,
            upper_bound=upper,
            periods=periods,
            model_type="prophet",
            parameters={k: v for k, v in params.items() if not k.startswith("_")},
            fit_metrics={
                "in_sample_mape": params.get("in_sample_mape", params.get("mape", 0)),
                "r_squared": params.get("r_squared", 0),
            },
            diagnostics={
                "seasonality_detected": params.get("yearly_seasonality", False),
                "seasonality_period": 12 if params.get("yearly_seasonality") else None,
                "has_changepoints": params.get("has_changepoints", False),
                "n_changepoints": params.get("n_changepoints", 0),
            },
        )
