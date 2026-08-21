from __future__ import annotations

"""Learned heuristics proposed by the reflection pass (M3)."""

from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.database import Base

HEURISTIC_KINDS = frozenset({"error_bias", "override_pattern"})
HEURISTIC_STATUSES = frozenset({"candidate", "active", "rejected", "superseded"})
HEURISTIC_SOURCES = frozenset({"actuals", "overrides"})


class LearnedHeuristic(Base):
    """A statistically-derived rule of thumb awaiting human review.

    Rows are written by the reflection pass as ``candidate`` and only become
    ``active`` after a reviewer approves them — nothing here feeds a forecast
    automatically.
    """

    __tablename__ = "learned_heuristics"

    # Transient, NOT persisted. app.services.reflection stamps these onto rows
    # it returns so the API can report whether an approved heuristic actually
    # feeds model selection, and which rows it superseded, without a second
    # query. Declared here (with __allow_unmapped__) so they are typed and
    # discoverable rather than materialising out of nowhere on an ORM instance.
    __allow_unmapped__ = True

    influences_selection: bool = False
    #: None means "not stamped"; callers should treat it as an empty list.
    superseded_ids: list[int] | None = None

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # line_item | category | global
    scope: Mapped[str] = mapped_column(String(32), nullable=False, default="line_item")
    line_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("line_items.id"), nullable=True, index=True
    )
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    model_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    horizon_bucket: Mapped[str | None] = mapped_column(String(20), nullable=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)  # error_bias|override_pattern
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    effect_size: Mapped[float | None] = mapped_column(Float, nullable=True)
    evidence: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # candidate|active|rejected|superseded
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="candidate")
    source: Mapped[str | None] = mapped_column(String(20), nullable=True)  # actuals|overrides
    proposed_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, default=lambda: datetime.now(timezone.utc)
    )
    approved_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # When this heuristic should be re-examined (staleness guard)
    review_by: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
