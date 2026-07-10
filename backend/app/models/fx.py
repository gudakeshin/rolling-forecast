from __future__ import annotations

"""FX rates and forecast accuracy vintage tracking models."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import String, DateTime, Float, Integer, ForeignKey, Text, Boolean, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class FxRate(Base):
    """Period FX rate from one currency to another."""

    __tablename__ = "fx_rates"
    __table_args__ = (
        UniqueConstraint("from_currency", "to_currency", "period", "rate_type", name="uq_fx_rate"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    from_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    to_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    period: Mapped[str] = mapped_column(String(16), nullable=False)  # YYYY-MM or FY label
    rate: Mapped[float] = mapped_column(Float, nullable=False)
    rate_type: Mapped[str] = mapped_column(String(20), default="average")  # average | spot | budget
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )


class SystemSetting(Base):
    """Simple key/value system settings (e.g. reporting_currency)."""

    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )


class ForecastAccuracyRecord(Base):
    """Vintage-based forecast vs actual observation for one line/period/horizon."""

    __tablename__ = "forecast_accuracy_records"
    __table_args__ = (
        UniqueConstraint(
            "version_id", "line_item_id", "period",
            name="uq_accuracy_version_line_period",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    version_id: Mapped[str] = mapped_column(ForeignKey("forecast_versions.id"), nullable=False, index=True)
    line_item_id: Mapped[int] = mapped_column(ForeignKey("line_items.id"), nullable=False, index=True)
    period: Mapped[str] = mapped_column(String(16), nullable=False)
    horizon_offset: Mapped[int] = mapped_column(Integer, nullable=False)  # months ahead of base_period
    predicted_p10: Mapped[float | None] = mapped_column(Float, nullable=True)
    predicted_p50: Mapped[float] = mapped_column(Float, nullable=False)
    predicted_p90: Mapped[float | None] = mapped_column(Float, nullable=True)
    actual: Mapped[float] = mapped_column(Float, nullable=False)
    absolute_error: Mapped[float] = mapped_column(Float, nullable=False)
    pct_error: Mapped[float | None] = mapped_column(Float, nullable=True)
    within_p10_p90: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    model_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
