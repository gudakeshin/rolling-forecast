from __future__ import annotations

"""Persistent core memory blocks (organization/user/BU/persona)."""

from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class MemoryBlock(Base):
    __tablename__ = "memory_blocks"
    __table_args__ = (
        UniqueConstraint(
            "scope",
            "owner_id",
            "label",
            name="uq_memory_blocks_scope_owner_label",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scope: Mapped[str] = mapped_column(String(32), nullable=False)  # persona|organization|user|business_unit
    owner_id: Mapped[str | None] = mapped_column(String(100), nullable=True)  # user_id or BU key
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    char_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=2000)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
