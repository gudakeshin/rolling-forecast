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
