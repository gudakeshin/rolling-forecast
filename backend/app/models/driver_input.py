"""Driver input models -- BU head assumption submissions."""

import uuid
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, Float, Integer, ForeignKey, Text, JSON, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class DriverFormConfig(Base):
    """Configuration for a BU-specific driver input form."""

    __tablename__ = "driver_form_configs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    business_unit: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Form field definitions as JSON array
    # Each field: {name, label, type, line_item_id, validation_rules, default_source}
    fields_schema: Mapped[dict] = mapped_column(JSON, nullable=False)

    # Deadline management
    soft_deadline_days: Mapped[int] = mapped_column(
        Integer, default=3
    )  # Days after cycle start
    hard_deadline_days: Mapped[int] = mapped_column(Integer, default=5)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class DriverInput(Base):
    """A submitted set of driver assumptions from a BU head."""

    __tablename__ = "driver_inputs"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    version_id: Mapped[str] = mapped_column(
        ForeignKey("forecast_versions.id"), nullable=False
    )
    form_config_id: Mapped[int] = mapped_column(
        ForeignKey("driver_form_configs.id"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    business_unit: Mapped[str] = mapped_column(String(100), nullable=False)

    # Submitted values as JSON: {field_name: {value, prior_value, model_suggested, reason}}
    values: Mapped[dict] = mapped_column(JSON, nullable=False)

    # Workflow
    status: Mapped[str] = mapped_column(
        String(50), default="submitted"
    )  # submitted, approved, rejected, late
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    reviewed_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    review_comments: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_late: Mapped[bool] = mapped_column(Boolean, default=False)

    # Relationships
    version: Mapped["ForecastVersion"] = relationship(back_populates="driver_inputs")
    user: Mapped["User"] = relationship(back_populates="driver_inputs")
