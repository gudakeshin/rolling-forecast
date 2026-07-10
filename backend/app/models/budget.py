"""Budget entities for forecast vs budget comparison."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import String, DateTime, Float, Integer, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class BudgetVersion(Base):
    """An annual (or periodic) budget snapshot."""

    __tablename__ = "budget_versions"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    fiscal_year: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="active")
    source_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    line_items: Mapped[list["BudgetLineItem"]] = relationship(
        back_populates="budget_version", cascade="all, delete-orphan"
    )


class BudgetLineItem(Base):
    """Budget value for a line item in a period."""

    __tablename__ = "budget_line_items"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    budget_version_id: Mapped[str] = mapped_column(
        ForeignKey("budget_versions.id"), nullable=False, index=True
    )
    line_item_id: Mapped[int] = mapped_column(
        ForeignKey("line_items.id"), nullable=False, index=True
    )
    period: Mapped[str] = mapped_column(String(7), nullable=False)  # YYYY-MM
    value: Mapped[float] = mapped_column(Float, nullable=False)

    budget_version: Mapped["BudgetVersion"] = relationship(back_populates="line_items")
