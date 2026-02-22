"""Actuals data models -- datasets and individual records."""

import uuid
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, Float, Integer, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class ActualsDataset(Base):
    """A batch of ingested actuals data with lineage tracking."""

    __tablename__ = "actuals_datasets"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    source_type: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # "csv", "api", "warehouse"
    source_name: Mapped[str] = mapped_column(
        String(255), default=""
    )  # filename or endpoint
    file_hash: Mapped[str] = mapped_column(
        String(64), nullable=False
    )  # SHA-256 of source data
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    period_start: Mapped[str] = mapped_column(
        String(7), nullable=False
    )  # "2023-01" format
    period_end: Mapped[str] = mapped_column(String(7), nullable=False)
    periods_count: Mapped[int] = mapped_column(Integer, default=0)
    missing_periods: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )  # JSON list
    completeness_pct: Mapped[float] = mapped_column(Float, default=100.0)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    records: Mapped[list["ActualsRecord"]] = relationship(
        back_populates="dataset", cascade="all, delete-orphan"
    )
    forecast_versions: Mapped[list["ForecastVersion"]] = relationship(
        back_populates="actuals_dataset"
    )


class ActualsRecord(Base):
    """Individual GL-level actual value for a line item in a period."""

    __tablename__ = "actuals_records"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    dataset_id: Mapped[str] = mapped_column(
        ForeignKey("actuals_datasets.id"), nullable=False
    )
    line_item_id: Mapped[int] = mapped_column(
        ForeignKey("line_items.id"), nullable=False
    )
    period: Mapped[str] = mapped_column(
        String(7), nullable=False
    )  # "2024-03" format
    value: Mapped[float] = mapped_column(Float, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="USD")

    dataset: Mapped["ActualsDataset"] = relationship(back_populates="records")
    line_item: Mapped["LineItem"] = relationship(back_populates="actuals_records")
