from __future__ import annotations

"""Named, saved forecast-panel views (a panel type + its filter params)."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.database import Base


class SavedView(Base):
    """A personal shortcut to reopen a panel with a specific set of filters.

    Unlike ModelPreset, there is no global/shared bucket here -- every view
    belongs to exactly one company (business_unit_id is not nullable) and is
    only visible to the user who created it -- see
    app/services/saved_views.py.
    """

    __tablename__ = "saved_views"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    panel_type: Mapped[str] = mapped_column(String(50), nullable=False)
    panel_params: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    business_unit_id: Mapped[str] = mapped_column(
        ForeignKey("business_units.id"), nullable=False
    )
    created_by: Mapped[str | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        UniqueConstraint(
            "name", "business_unit_id", "created_by", name="uq_saved_views_name_bu_user"
        ),
    )
