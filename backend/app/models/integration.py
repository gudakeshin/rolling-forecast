from __future__ import annotations

"""Integration connection registry — Fernet-encrypted URLs, admin-managed."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import String, DateTime, Boolean, Text, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class IntegrationConnection(Base):
    """Named, admin-managed connection for warehouse/ERP pulls.

    Secrets (encrypted_url, encrypted_token) are write-only via the API —
    list/get responses never return decrypted values.
    """

    __tablename__ = "integration_connections"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(50), nullable=False)  # warehouse | erp
    # Fernet ciphertext of SQLAlchemy URL (warehouse) or base URL (erp)
    encrypted_url: Mapped[str] = mapped_column(Text, nullable=False)
    # Optional Fernet ciphertext of bearer token (erp)
    encrypted_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
