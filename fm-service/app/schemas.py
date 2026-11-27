"""Wire contract for the fm-service batch forecast endpoint.

The backend client (backend/app/services/foundation_client.py) speaks exactly
this shape; bump SERVICE_VERSION on any breaking change so stored provenance
says which contract produced a forecast.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

SERVICE_VERSION = "1.0.0"
QUANTILE_LEVELS: tuple[float, float, float] = (0.1, 0.5, 0.9)
MAX_SERIES_PER_REQUEST = 5000
MAX_PREDICTION_LENGTH = 64
MAX_CONTEXT = 2048


class SeriesInput(BaseModel):
    """One forecasting task: a target history plus optional covariates.

    ``past_covariates[name]`` has the same length as ``target``.
    ``future_covariates[name]`` has length ``prediction_length`` and every
    future key must also be a past key (Chronos-2's own rule): a known-future
    driver needs its history too. A past-only covariate simply has no future
    entry.
    """

    id: str = Field(min_length=1, max_length=200)
    target: list[float | None] = Field(min_length=1, max_length=MAX_CONTEXT)
    prediction_length: int = Field(ge=1, le=MAX_PREDICTION_LENGTH)
    past_covariates: dict[str, list[float | None]] = Field(default_factory=dict)
    future_covariates: dict[str, list[float | None]] = Field(default_factory=dict)
    # Series sharing a group id are predicted jointly (Chronos-2 group
    # attention). Callers must only group series whose contexts end at the
    # same point in time, or a later context can leak into an earlier one.
    group_id: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def _check_lengths(self) -> "SeriesInput":
        n = len(self.target)
        for name, values in self.past_covariates.items():
            if len(values) != n:
                raise ValueError(
                    f"series {self.id}: past covariate {name!r} has {len(values)} values, target has {n}"
                )
        for name, values in self.future_covariates.items():
            if name not in self.past_covariates:
                raise ValueError(
                    f"series {self.id}: future covariate {name!r} has no past values"
                )
            if len(values) != self.prediction_length:
                raise ValueError(
                    f"series {self.id}: future covariate {name!r} has {len(values)} values, "
                    f"prediction_length is {self.prediction_length}"
                )
        return self


class ForecastRequest(BaseModel):
    series: list[SeriesInput] = Field(min_length=1, max_length=MAX_SERIES_PER_REQUEST)

    @model_validator(mode="after")
    def _unique_ids(self) -> "ForecastRequest":
        ids = [s.id for s in self.series]
        if len(set(ids)) != len(ids):
            raise ValueError("series ids must be unique within a request")
        return self


class SeriesForecast(BaseModel):
    id: str
    p10: list[float]
    p50: list[float]
    p90: list[float]


class ModelInfo(BaseModel):
    model_id: str
    model_revision: str
    service_version: str
    library_version: str
    quantiles: list[float]


class ForecastResponse(BaseModel):
    model: ModelInfo
    forecasts: list[SeriesForecast]
