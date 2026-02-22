"""Override model -- tracks human overrides to forecast values."""

import uuid
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, Float, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class Override(Base):
    """A human override of a model-generated forecast value."""

    __tablename__ = "overrides"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    version_id: Mapped[str] = mapped_column(
        ForeignKey("forecast_versions.id"), nullable=False
    )
    line_item_id: Mapped[int] = mapped_column(
        ForeignKey("line_items.id"), nullable=False
    )
    period: Mapped[str] = mapped_column(String(7), nullable=False)

    # Values
    original_model_value: Mapped[float] = mapped_column(Float, nullable=False)
    override_value: Mapped[float] = mapped_column(Float, nullable=False)
    reason: Mapped[str] = mapped_column(
        Text, nullable=False
    )  # Minimum 10 characters required

    # Status
    status: Mapped[str] = mapped_column(
        String(50), default="active"
    )  # active, reverted, superseded
    carry_forward: Mapped[bool] = mapped_column(
        default=True
    )  # Persist to next cycle?

    # Audit
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    reverted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reverted_by: Mapped[str | None] = mapped_column(String(36), nullable=True)

    # How many downstream lines were recalculated
    downstream_recalc_count: Mapped[int] = mapped_column(default=0)

    # Relationships
    version: Mapped["ForecastVersion"] = relationship(back_populates="overrides")
    line_item: Mapped["LineItem"] = relationship(back_populates="overrides")
    user: Mapped["User"] = relationship(back_populates="overrides")
