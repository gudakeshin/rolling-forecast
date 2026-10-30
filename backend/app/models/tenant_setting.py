from __future__ import annotations

"""Per-company overrides of otherwise-global settings.

`app.models.fx.SystemSetting` is a single global key/value row (e.g.
reporting_currency, fiscal_calendar) shared by every company. This table
holds company-specific overrides of the same keys; the global row stays the
fallback default when no override exists for a given business_unit_id. See
app/services/tenant_settings.py for the read/write helpers.
"""

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class BusinessUnitSetting(Base):
    """A company's override of one otherwise-global setting key."""

    __tablename__ = "business_unit_settings"

    business_unit_id: Mapped[str] = mapped_column(
        ForeignKey("business_units.id"), primary_key=True
    )
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
