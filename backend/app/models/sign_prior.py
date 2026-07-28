from __future__ import annotations

"""Admin-editable sign priors for driver discovery (Phase 9 follow-up).

A prior says which way a driver is *allowed* to move a line family — e.g.
headcount cannot reduce payroll. Discovery reads this table and falls back to
``DEFAULT_SIGN_PRIORS`` when a pair is absent, so an empty table behaves exactly
like the hardcoded defaults.
"""

from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

SIGN_PRIOR_FAMILIES = frozenset({"revenue", "expense"})
SIGN_PRIOR_SIGNS = frozenset({-1, 1})


class SignPrior(Base):
    """Expected coefficient sign for one (driver_type, line_family) pair."""

    __tablename__ = "sign_priors"
    __table_args__ = (
        UniqueConstraint(
            "driver_type", "line_family", name="uq_sign_priors_type_family"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    driver_type: Mapped[str] = mapped_column(String(50), nullable=False)
    # revenue | expense
    line_family: Mapped[str] = mapped_column(String(20), nullable=False)
    # -1 | 1 — "no opinion" is expressed by the row being absent, not by 0
    expected_sign: Mapped[int] = mapped_column(Integer, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
