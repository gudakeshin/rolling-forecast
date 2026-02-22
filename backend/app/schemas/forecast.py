"""Pydantic schemas for forecast-related endpoints."""

from pydantic import BaseModel, Field
from typing import Any
from datetime import datetime


class ForecastVersionResponse(BaseModel):
    id: str
    name: str
    label: str | None
    status: str
    version_type: str
    horizon_months: int
    base_period: str | None
    total_line_items: int
    high_confidence_count: int
    medium_confidence_count: int
    low_confidence_count: int
    override_count: int
    generation_time_seconds: float | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ForecastLineResultResponse(BaseModel):
    id: str
    line_item_id: int
    line_item_name: str = ""
    line_item_category: str = ""
    period: str
    p10: float | None
    p50: float
    p90: float | None
    confidence_score: float
    confidence_level: str
    model_type: str | None
    is_overridden: bool
    override_value: float | None

    model_config = {"from_attributes": True}


class OverrideRequest(BaseModel):
    version_id: str
    line_item_id: int
    period: str
    override_value: float
    reason: str = Field(..., min_length=10)


class OverrideResponse(BaseModel):
    id: str
    line_item_id: int
    period: str
    original_model_value: float
    override_value: float
    reason: str
    status: str
    user_id: str
    created_at: datetime
    downstream_recalc_count: int

    model_config = {"from_attributes": True}


class VarianceItem(BaseModel):
    line_item_id: int
    line_item_name: str
    category: str
    current_value: float
    prior_value: float | None
    budget_value: float | None
    ytd_actual: float | None
    variance_abs: float | None
    variance_pct: float | None
    is_favorable: bool | None


class ComparisonResponse(BaseModel):
    current_version: str
    prior_version: str | None
    items: list[VarianceItem] = Field(default_factory=list)
    top_variances: list[VarianceItem] = Field(default_factory=list)


class PanelDataResponse(BaseModel):
    """Generic response for side panel data requests."""
    panel_type: str
    title: str
    data: dict[str, Any] = Field(default_factory=dict)
