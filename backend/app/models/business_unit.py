from __future__ import annotations

"""BusinessUnit -- the enforced tenant boundary; belongs to a Company.

Hardens the free-text `business_unit` string that used to live directly on
User/LineItem/Driver/DriverInput/DriverFormConfig into a real, FK-scoped
entity. See app/services/permissions.py for enforcement and
app/services/actuals_resolution.py for how it scopes "current dataset".

Company (app/models/company.py) is the formal tenant root as of migration
027_company; a BusinessUnit belongs to exactly one Company via company_id.
Today every BusinessUnit is still 1:1 with its Company (each was its own
tenant already), and enforcement still keys off business_unit_id everywhere
-- company_id is schema-only groundwork for real multi-BU-per-company
support, not yet a second scoping dimension anything checks.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.company import Company

# Legacy rows with no business_unit assigned (and any distinct free-text
# value that predates this table) get backfilled into this bucket rather
# than left NULL -- there is no "shared, visible to everyone" scope anymore.
DEFAULT_BUSINESS_UNIT_NAME = "Default"


class BusinessUnit(Base):
    """A company/tenant whose data must never mix with another's."""

    __tablename__ = "business_units"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    company_id: Mapped[str | None] = mapped_column(
        ForeignKey("companies.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    company: Mapped["Company"] = relationship()
