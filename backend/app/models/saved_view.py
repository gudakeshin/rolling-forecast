from __future__ import annotations

"""SavedView -- a named panel + filter combination an analyst can reopen."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class SavedView(Base):
    """Owned by one user within one company.

    ``business_unit_id`` is the company the view was saved in: its panel
    params (version ids, line items, ...) only mean something there, so a
    view is listed only while the owner -- or, when ``is_shared``, any member
    -- is working in that company. Tenant-scoped like the other
    ``business_unit_id`` models (app/services/tenant_scope.py).
    """

    __tablename__ = "saved_views"
    __table_args__ = (Index("ix_saved_views_bu_user", "business_unit_id", "user_id"),)

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    business_unit_id: Mapped[str | None] = mapped_column(
        ForeignKey("business_units.id"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    panel_type: Mapped[str] = mapped_column(String(64), nullable=False)
    panel_params: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    is_shared: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
