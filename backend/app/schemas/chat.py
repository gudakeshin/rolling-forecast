"""Pydantic schemas for chat messages and streaming."""

from pydantic import BaseModel, Field
from typing import Any
from datetime import datetime


# ---------- Content Blocks ----------

class ContentBlock(BaseModel):
    """A single content block within a chat message."""
    type: str = Field(
        ...,
        description="Block type: text, table, chart, status, action, panel_trigger",
    )
    data: dict[str, Any] = Field(default_factory=dict)


class TextBlock(ContentBlock):
    type: str = "text"
    data: dict[str, Any] = Field(default_factory=lambda: {"text": ""})


class TableBlock(ContentBlock):
    type: str = "table"
    # data: {title, columns: [{key, label}], rows: [{key: value}]}


class ChartBlock(ContentBlock):
    type: str = "chart"
    # data: {chart_type, title, series: [...], x_axis, y_axis}


class StatusBlock(ContentBlock):
    type: str = "status"
    # data: {label, progress (0-1), step, is_complete}


class ActionBlock(ContentBlock):
    type: str = "action"
    # data: {actions: [{id, label, variant}]}


class PanelTriggerBlock(ContentBlock):
    type: str = "panel_trigger"
    # data: {panel, params, label}


# ---------- Messages ----------

class ChatMessageRequest(BaseModel):
    """User message sent to the chat endpoint."""
    conversation_id: str | None = None  # None for new conversation
    content: str
    attachments: list[str] = Field(default_factory=list)  # File paths


class ChatMessageResponse(BaseModel):
    """Agent response message."""
    id: str
    conversation_id: str
    role: str = "assistant"
    content: str  # Plain text summary
    content_blocks: list[ContentBlock] = Field(default_factory=list)
    tool_calls: list[dict[str, Any]] | None = None
    panel_payload: dict[str, Any] | None = None
    created_at: datetime


class StreamEvent(BaseModel):
    """Server-Sent Event payload for streaming responses."""
    event: str = Field(
        ...,
        description="Event type: message_start, content_block, tool_start, tool_end, message_end, error",
    )
    data: dict[str, Any] = Field(default_factory=dict)


class ConversationSummary(BaseModel):
    """Summary of a conversation for the sidebar."""
    id: str
    title: str
    created_at: datetime
    updated_at: datetime
    message_count: int = 0


class ConversationDetail(BaseModel):
    """Full conversation with messages."""
    id: str
    title: str
    messages: list[ChatMessageResponse] = Field(default_factory=list)
    working_memory: dict[str, Any] | None = None
