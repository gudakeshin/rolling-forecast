from __future__ import annotations

"""User and Role models for RBAC."""

import uuid
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, ForeignKey, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(String(255), default="")
    # Permission flags
    can_input: Mapped[bool] = mapped_column(Boolean, default=False)  # Submit driver assumptions
    can_generate: Mapped[bool] = mapped_column(Boolean, default=False)  # Generate forecasts
    can_override: Mapped[bool] = mapped_column(Boolean, default=False)  # Override values
    can_review: Mapped[bool] = mapped_column(Boolean, default=False)  # Approve/reject
    can_publish: Mapped[bool] = mapped_column(Boolean, default=False)  # Publish to stakeholders
    can_admin: Mapped[bool] = mapped_column(Boolean, default=False)  # Configure system
    can_view_all_bus: Mapped[bool] = mapped_column(
        Boolean, default=False
    )  # Cross-BU read access (admins default True)
    can_manage_drivers: Mapped[bool] = mapped_column(
        Boolean, default=False
    )  # Causal driver CRUD / ingest

    users: Mapped[list["User"]] = relationship(back_populates="role")


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    username: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), default="")
    business_unit: Mapped[str | None] = mapped_column(String(100), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    role: Mapped["Role"] = relationship(back_populates="users")
    conversations: Mapped[list["Conversation"]] = relationship(back_populates="user")
    overrides: Mapped[list["Override"]] = relationship(back_populates="user")
    driver_inputs: Mapped[list["DriverInput"]] = relationship(back_populates="user")

    @property
    def role_name(self) -> str:
        return self.role.name if self.role else ""

    @property
    def can_input(self) -> bool:
        return bool(self.role and self.role.can_input)

    @property
    def can_generate(self) -> bool:
        return bool(self.role and self.role.can_generate)

    @property
    def can_override(self) -> bool:
        return bool(self.role and self.role.can_override)

    @property
    def can_review(self) -> bool:
        return bool(self.role and self.role.can_review)

    @property
    def can_publish(self) -> bool:
        return bool(self.role and self.role.can_publish)

    @property
    def can_admin(self) -> bool:
        return bool(self.role and self.role.can_admin)

    @property
    def can_manage_drivers(self) -> bool:
        return bool(self.role and getattr(self.role, "can_manage_drivers", False))
