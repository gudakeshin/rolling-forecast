from __future__ import annotations

"""Company -- the real tenant root.

BusinessUnit was overloaded as both "the tenant/company boundary" and the
domain's actual business-unit-within-a-company concept (see
app/models/business_unit.py). Company formalizes the tenant root; a
BusinessUnit belongs to exactly one Company via business_unit_id.

This is schema-only groundwork (migration 027_company): enforcement still
keys off business_unit_id everywhere (see app/services/permissions.py), and
today every BusinessUnit is still 1:1 with its Company. Real multi-BU-per-
company support is a later pass.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Company(Base):
    """A tenant whose data must never mix with another's."""

    __tablename__ = "companies"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
