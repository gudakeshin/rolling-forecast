from __future__ import annotations

"""AnomalyDismissal -- per-user review state for an anomaly-dashboard finding."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AnomalyDismissal(Base):
    """Records that a user has reviewed/dismissed an anomaly finding.

    ``anomaly_id`` is the id of the ForecastLineResult row the anomaly
    dashboard groups findings around (see get_anomaly_dashboard in
    app/api/dashboard.py) -- there is no separate anomaly entity.
    """

    __tablename__ = "anomaly_dismissals"
    __table_args__ = (
        UniqueConstraint("user_id", "anomaly_id", name="uq_anomaly_dismissals_user_anomaly"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    version_id: Mapped[str] = mapped_column(
        ForeignKey("forecast_versions.id"), nullable=False
    )
    anomaly_id: Mapped[str] = mapped_column(String(36), nullable=False)
    dismissed_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
