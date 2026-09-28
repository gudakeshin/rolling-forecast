from __future__ import annotations

"""Multi-level approval workflow with segregation of duties."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import String, DateTime, Integer, ForeignKey, Text, Boolean, JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ApprovalWorkflow(Base):
    """Configurable multi-level approval chain for forecast versions."""

    __tablename__ = "approval_workflows"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Ordered list: [{"level": 1, "role": "reviewer", "label": "Finance Director"}, ...]
    levels: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    require_sod: Mapped[bool] = mapped_column(Boolean, default=True)  # SoD: submitter ≠ approver
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )


class ApprovalStep(Base):
    """One approval decision at a workflow level for a forecast version."""

    __tablename__ = "approval_steps"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    version_id: Mapped[str] = mapped_column(
        ForeignKey("forecast_versions.id"), nullable=False, index=True
    )
    workflow_id: Mapped[str] = mapped_column(
        ForeignKey("approval_workflows.id"), nullable=False
    )
    level: Mapped[int] = mapped_column(Integer, nullable=False)
    required_role: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(
        String(50), default="pending"
    )  # pending, approved, rejected, skipped
    actor_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    comments: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
