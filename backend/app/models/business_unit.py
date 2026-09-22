from __future__ import annotations

"""BusinessUnit -- the company/tenant boundary.

Hardens the free-text `business_unit` string that used to live directly on
User/LineItem/Driver/DriverInput/DriverFormConfig into a real, FK-scoped
entity. See app/services/permissions.py for enforcement and
app/services/actuals_resolution.py for how it scopes "current dataset".
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

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
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
