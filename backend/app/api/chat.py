"""Chat endpoints -- send messages and stream responses via SSE."""

import json
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse

from app.database import get_db, SessionLocal
from app.api.auth import get_current_user
from app.models.user import User
from app.models.conversation import Conversation, Message
from app.schemas.chat import (
    ChatMessageRequest,
    ChatMessageResponse,
    ConversationSummary,
    ConversationDetail,
)
from app.orchestration.master_agent import MasterAgent
from app.orchestration.context_manager import ContextManager

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("/message")
async def send_message(
    request: ChatMessageRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Send a message and get a streaming SSE response."""
    # Eagerly capture user info before the DI scope closes
    user_id = current_user.id
    _ = current_user.role  # force-load the lazy relationship

    # Get or create conversation
    if request.conversation_id:
        conversation = (
            db.query(Conversation)
            .filter(
                Conversation.id == request.conversation_id,
                Conversation.user_id == user_id,
            )
            .first()
        )
        if not conversation:
            conversation = Conversation(user_id=user_id)
            db.add(conversation)
            db.commit()
            db.refresh(conversation)
    else:
        conversation = Conversation(user_id=user_id)
        db.add(conversation)
        db.commit()
        db.refresh(conversation)

    conversation_id = conversation.id
    message_content = request.content

    # Save user message
    user_message = Message(
        conversation_id=conversation_id,
        role="user",
        content=message_content,
    )
    db.add(user_message)
    db.commit()

    # Detach objects so they survive beyond the DI session
    db.expunge(current_user)
    db.expunge(conversation)

    async def event_generator():
        """SSE event stream — uses its own DB session to stay alive for the full stream."""
        stream_db = SessionLocal()
        try:
            # Re-attach objects to the stream-scoped session
            stream_db.add(current_user)
            stream_db.add(conversation)

            context_manager = ContextManager(db=stream_db, conversation=conversation, user=current_user)
            agent = MasterAgent(context_manager=context_manager, db=stream_db)

            yield {
                "event": "message_start",
                "data": json.dumps({"conversation_id": conversation_id}),
            }

            full_response = None
            async for event in agent.astream(message_content):
                yield {
                    "event": event["event"],
                    "data": json.dumps(event["data"]),
                }
                if event["event"] == "message_end":
                    full_response = event["data"]

            if full_response:
                assistant_message = Message(
                    conversation_id=conversation_id,
                    role="assistant",
                    content=full_response.get("content", ""),
                    content_blocks=full_response.get("content_blocks"),
                    tool_calls=full_response.get("tool_calls"),
                    panel_payload=full_response.get("panel_payload"),
                )
                stream_db.add(assistant_message)

                conv = stream_db.query(Conversation).get(conversation_id)
                if conv and len(conv.messages) <= 2:
                    conv.title = message_content[:100]

                stream_db.commit()

        except Exception as e:
            yield {
                "event": "error",
                "data": json.dumps({"error": str(e)}),
            }
        finally:
            stream_db.close()

    return EventSourceResponse(event_generator())


@router.get("/conversations", response_model=list[ConversationSummary])
async def list_conversations(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List all conversations for the current user."""
    conversations = (
        db.query(Conversation)
        .filter(Conversation.user_id == current_user.id)
        .order_by(Conversation.updated_at.desc())
        .all()
    )
    return [
        ConversationSummary(
            id=c.id,
            title=c.title,
            created_at=c.created_at,
            updated_at=c.updated_at,
            message_count=len(c.messages),
        )
        for c in conversations
    ]


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(
    conversation_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get full conversation with all messages."""
    conversation = (
        db.query(Conversation)
        .filter(
            Conversation.id == conversation_id,
            Conversation.user_id == current_user.id,
        )
        .first()
    )
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    return ConversationDetail(
        id=conversation.id,
        title=conversation.title,
        messages=[
            ChatMessageResponse(
                id=m.id,
                conversation_id=conversation.id,
                role=m.role,
                content=m.content,
                content_blocks=m.content_blocks or [],
                tool_calls=m.tool_calls,
                panel_payload=m.panel_payload,
                created_at=m.created_at,
            )
            for m in conversation.messages
        ],
        working_memory=conversation.working_memory,
    )
