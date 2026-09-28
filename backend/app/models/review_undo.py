from __future__ import annotations

"""ReviewUndoSnapshot -- pre-mutation state for one undoable review action.

review_item / batch_review / accept_ai_recommendations overwrite review_status
etc. in place with no history. This table captures the exact prior value of
every ForecastLineResult row a single action is about to touch, so the
frontend's undo toast is backed by a real reversal instead of a client-side
guess at what the fields used to be.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ReviewUndoSnapshot(Base):
    """One undoable review action and the prior field values it overwrote.

    ``consumed_at`` is set the moment an undo succeeds so a stale "Undo" click
    (a second tab, a slow network) can't replay and silently revert someone
    else's later edit. ``expires_at`` bounds how long the action stays
    reversible -- matches the toast's own lifetime, plus margin.
    """

    __tablename__ = "review_undo_snapshots"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    version_id: Mapped[str] = mapped_column(
        ForeignKey("forecast_versions.id"), nullable=False
    )
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    # review_item | batch_review | accept_ai_recommendations
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[str] = mapped_column(String(255), nullable=False)
    # [{id, review_status, review_comment, reviewed_by, reviewed_at (iso|None)}, ...]
    prior_state: Mapped[list] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
