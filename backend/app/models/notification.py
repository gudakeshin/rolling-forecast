from __future__ import annotations

"""Notification -- an actionable event surfaced to one user (the header bell)."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Notification(Base):
    """A single notification for one user.

    ``dedup_key`` lets a trigger be safely re-run (a nightly sweep, a retried
    job) without spamming duplicates: it's unique per (user, dedup_key), so a
    second attempt to create the same logical event is a silent no-op. Rows
    with no dedup_key (none currently) are unrestricted.
    """

    __tablename__ = "notifications"
    __table_args__ = (
        Index("ix_notifications_user_created", "user_id", "created_at"),
        UniqueConstraint("user_id", "dedup_key", name="uq_notifications_user_dedup"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    # approval_pending | actuals_ingested | driver_overdue | anomaly_detected | job_finished
    kind: Mapped[str] = mapped_column(String(50), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    entity_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    entity_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Panel to open (and its params) when the notification is clicked.
    link_panel: Mapped[str | None] = mapped_column(String(50), nullable=True)
    link_params: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    dedup_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), nullable=False
    )
