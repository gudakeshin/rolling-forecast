from __future__ import annotations

"""KpiDefinition -- a company's custom KPI, a formula over its P&L lines."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

KPI_DISPLAY_FORMATS = ("number", "currency", "percent", "ratio")


class KpiDefinition(Base):
    """Formula syntax and evaluation: app/domain/kpi_formula.py and
    app/services/kpis.py. Tenant-scoped by ``business_unit_id`` like the other
    company-owned models (app/services/tenant_scope.py)."""

    __tablename__ = "kpi_definitions"
    __table_args__ = (
        UniqueConstraint("business_unit_id", "name", name="uq_kpi_definitions_bu_name"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    business_unit_id: Mapped[str] = mapped_column(
        ForeignKey("business_units.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    formula: Mapped[str] = mapped_column(Text, nullable=False)
    display_format: Mapped[str] = mapped_column(String(16), nullable=False, default="number")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
