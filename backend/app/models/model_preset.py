from __future__ import annotations

"""Named, saved forecasting model configurations (presets)."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.database import Base


class ModelPreset(Base):
    """A saved shortcut for `generate_baseline`'s model_type/models_to_test params.

    Purely a named convenience layer over the existing model registry — it
    does not add new algorithms or change forecasting behavior. Validity of
    ``model_type``/``candidate_models`` against the live registry is checked
    only when a preset is *resolved* for a run (registry membership is
    env-flag-dependent), never at create/update time.
    """

    __tablename__ = "model_presets"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 'auto' (registry auto-select, optionally restricted by candidate_models)
    # or a specific registry model name pinned for ALL line items.
    model_type: Mapped[str] = mapped_column(String(50), nullable=False, default="auto")
    # Candidate pool restriction for auto-selection; only meaningful when
    # model_type == "auto". A list of registry model names, or None (no restriction).
    candidate_models: Mapped[list | None] = mapped_column(JSON, nullable=True)
    default_horizon_months: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    created_by: Mapped[str | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (UniqueConstraint("name", name="uq_model_presets_name"),)
