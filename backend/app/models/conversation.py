from __future__ import annotations

"""Conversation and message models for the chat interface."""

import uuid
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, ForeignKey, Text, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class Conversation(Base):
    """A chat conversation between a user and the forecast agent."""

    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(255), default="New Conversation")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Working memory: current context for this conversation
    # e.g., {"active_version_id": "...", "active_bu": "North America"}
    working_memory: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    user: Mapped["User"] = relationship(back_populates="conversations")
    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.created_at",
    )


class Message(Base):
    """A single message in a conversation."""

    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id"), nullable=False
    )
    role: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # "user", "assistant", "system"
    content: Mapped[str] = mapped_column(Text, nullable=False)

    # Rich content blocks as JSON array for structured display
    # Each block: {type: "text"|"table"|"chart"|"status"|"action"|"panel_trigger", data: {...}}
    content_blocks: Mapped[list | None] = mapped_column(JSON, nullable=True)

    # Tool/skill invocation details (for agent messages)
    tool_calls: Mapped[list | None] = mapped_column(
        JSON, nullable=True
    )  # Skills that were invoked
    panel_payload: Mapped[dict | None] = mapped_column(
        JSON, nullable=True
    )  # Side panel data

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    conversation: Mapped["Conversation"] = relationship(back_populates="messages")
