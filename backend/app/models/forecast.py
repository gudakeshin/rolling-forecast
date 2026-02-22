"""Forecast version, line results, and model metadata models."""

import uuid
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, Float, Integer, Boolean, ForeignKey, Text, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class ForecastVersion(Base):
    """An immutable snapshot of a complete forecast."""

    __tablename__ = "forecast_versions"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    name: Mapped[str] = mapped_column(
        String(100), nullable=False
    )  # e.g., "FC-2026-02-v1"
    label: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )  # Optional custom label
    status: Mapped[str] = mapped_column(
        String(50), default="draft"
    )  # draft, in_review, approved, published, archived
    version_type: Mapped[str] = mapped_column(
        String(50), default="scheduled"
    )  # scheduled, ad_hoc, branch
    parent_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("forecast_versions.id"), nullable=True
    )  # For branches

    # Data lineage
    actuals_dataset_id: Mapped[str | None] = mapped_column(
        ForeignKey("actuals_datasets.id"), nullable=True
    )
    actuals_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    input_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )  # Hash of all inputs for idempotency

    # Forecast parameters
    horizon_months: Mapped[int] = mapped_column(Integer, default=12)
    base_period: Mapped[str | None] = mapped_column(
        String(7), nullable=True
    )  # Last actuals period
    model_versions: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    random_seed: Mapped[int] = mapped_column(Integer, default=42)

    # Metadata
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    created_by: Mapped[str | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Summary stats (denormalized for quick access)
    total_line_items: Mapped[int] = mapped_column(Integer, default=0)
    high_confidence_count: Mapped[int] = mapped_column(Integer, default=0)
    medium_confidence_count: Mapped[int] = mapped_column(Integer, default=0)
    low_confidence_count: Mapped[int] = mapped_column(Integer, default=0)
    override_count: Mapped[int] = mapped_column(Integer, default=0)
    generation_time_seconds: Mapped[float | None] = mapped_column(
        Float, nullable=True
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    actuals_dataset: Mapped["ActualsDataset"] = relationship(
        back_populates="forecast_versions"
    )
    line_results: Mapped[list["ForecastLineResult"]] = relationship(
        back_populates="version", cascade="all, delete-orphan"
    )
    overrides: Mapped[list["Override"]] = relationship(
        back_populates="version", cascade="all, delete-orphan"
    )
    driver_inputs: Mapped[list["DriverInput"]] = relationship(
        back_populates="version", cascade="all, delete-orphan"
    )


class ForecastLineResult(Base):
    """Forecast output for a single line item in a single period."""

    __tablename__ = "forecast_line_results"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    version_id: Mapped[str] = mapped_column(
        ForeignKey("forecast_versions.id"), nullable=False
    )
    line_item_id: Mapped[int] = mapped_column(
        ForeignKey("line_items.id"), nullable=False
    )
    period: Mapped[str] = mapped_column(String(7), nullable=False)  # "2026-03"

    # Forecast values
    p10: Mapped[float | None] = mapped_column(Float, nullable=True)  # 10th percentile
    p50: Mapped[float] = mapped_column(Float, nullable=False)  # Point forecast (median)
    p90: Mapped[float | None] = mapped_column(Float, nullable=True)  # 90th percentile

    # Confidence
    confidence_score: Mapped[float] = mapped_column(
        Float, default=0.0
    )  # 0-100 composite
    confidence_level: Mapped[str] = mapped_column(
        String(20), default="low"
    )  # high, medium, low

    # Model info
    model_type: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )  # "arima", "prophet", "ets", "linear", "ensemble"
    model_mape: Mapped[float | None] = mapped_column(Float, nullable=True)
    model_r_squared: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Override tracking
    is_overridden: Mapped[bool] = mapped_column(Boolean, default=False)
    override_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_calculated: Mapped[bool] = mapped_column(
        Boolean, default=False
    )  # True if computed from DAG

    # Review tracking (per-item review)
    review_status: Mapped[str | None] = mapped_column(
        String(20), nullable=True, default=None
    )  # approved, rejected, pending, None (unreviewed)
    review_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # AI review analysis
    ai_recommendation: Mapped[str | None] = mapped_column(
        String(20), nullable=True
    )  # approve, review, override, flag
    ai_reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_risk_score: Mapped[float | None] = mapped_column(
        Float, nullable=True
    )  # 0-100, higher = more risk/needs attention

    # Relationships
    version: Mapped["ForecastVersion"] = relationship(back_populates="line_results")
    line_item: Mapped["LineItem"] = relationship(back_populates="forecast_results")
    model_metadata: Mapped["ModelMetadata | None"] = relationship(
        back_populates="line_result", uselist=False
    )


class ModelMetadata(Base):
    """Detailed model training metadata for a forecast line result."""

    __tablename__ = "model_metadata"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    line_result_id: Mapped[str] = mapped_column(
        ForeignKey("forecast_line_results.id"), nullable=False
    )

    model_type: Mapped[str] = mapped_column(String(50), nullable=False)
    parameters: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    training_window_start: Mapped[str | None] = mapped_column(
        String(7), nullable=True
    )
    training_window_end: Mapped[str | None] = mapped_column(String(7), nullable=True)
    training_points: Mapped[int] = mapped_column(Integer, default=0)

    # Fit metrics
    mape: Mapped[float | None] = mapped_column(Float, nullable=True)
    r_squared: Mapped[float | None] = mapped_column(Float, nullable=True)
    aic: Mapped[float | None] = mapped_column(Float, nullable=True)
    bic: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Diagnostics
    seasonality_detected: Mapped[bool] = mapped_column(Boolean, default=False)
    seasonality_period: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )  # e.g., 12 for monthly
    structural_break_detected: Mapped[bool] = mapped_column(Boolean, default=False)
    structural_break_period: Mapped[str | None] = mapped_column(
        String(7), nullable=True
    )
    random_seed: Mapped[int] = mapped_column(Integer, default=42)

    # Relationships
    line_result: Mapped["ForecastLineResult"] = relationship(
        back_populates="model_metadata"
    )
