"""Chat endpoints -- send messages and stream responses via SSE."""

import asyncio
import json
from fastapi import APIRouter, Depends, HTTPException, Request
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
from app.rate_limit import limiter

router = APIRouter(prefix="/chat", tags=["chat"])

# Strong references to agent turns still running after their client left;
# without one, the event loop may garbage-collect a pending task.
_running_turns: set[asyncio.Task] = set()


@router.post("/message")
@limiter.limit("30/minute")
async def send_message(
    request: Request,
    body: ChatMessageRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Send a message and get a streaming SSE response."""
    # Eagerly capture user info before the DI scope closes
    user_id = current_user.id
    _ = current_user.role  # force-load the lazy relationship

    # Get or create conversation
    if body.conversation_id:
        conversation = (
            db.query(Conversation)
            .filter(
                Conversation.id == body.conversation_id,
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
    message_content = body.content

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

    queue: asyncio.Queue = asyncio.Queue()

    async def run_turn() -> None:
        """Run the agent turn to completion and save the reply, even if the
        client disconnects mid-stream. The SSE generator below only relays
        events; a client that lost its connection recovers the reply by
        reloading the conversation (frontend/src/api/chat.ts)."""
        stream_db = SessionLocal()
        try:
            # Re-attach objects to the turn-scoped session
            stream_db.add(current_user)
            stream_db.add(conversation)

            context_manager = ContextManager(db=stream_db, conversation=conversation, user=current_user)
            agent = MasterAgent(context_manager=context_manager, db=stream_db)

            await queue.put({
                "event": "message_start",
                "data": json.dumps({"conversation_id": conversation_id}),
            })

            full_response = None
            async for event in agent.astream(message_content):
                await queue.put({
                    "event": event["event"],
                    "data": json.dumps(event["data"]),
                })
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
            await queue.put({
                "event": "error",
                "data": json.dumps({"error": str(e)}),
            })
        finally:
            stream_db.close()
            await queue.put(None)

    task = asyncio.create_task(run_turn())
    _running_turns.add(task)
    task.add_done_callback(_running_turns.discard)

    async def event_generator():
        while True:
            item = await queue.get()
            if item is None:
                break
            yield item

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


@router.delete("/conversations/{conversation_id}", status_code=204)
async def delete_conversation(
    conversation_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete a conversation and all of its messages (owner only)."""
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

    # Detach documents scoped to this conversation (FK is nullable, no cascade)
    try:
        from app.models.document import Document

        db.query(Document).filter(Document.conversation_id == conversation_id).update(
            {"conversation_id": None},
            synchronize_session=False,
        )
    except Exception:
        pass

    db.delete(conversation)
    db.commit()
    return None


def _pending_or_404(action_id: str, user: User) -> dict:
    from app.services import pending_actions

    record = pending_actions.take_pending(action_id, user_id=user.id)
    if record is None:
        raise HTTPException(404, "This approval has expired or was already used")
    return record


@router.post("/pending-actions/{action_id}/confirm")
@limiter.limit("30/minute")
async def confirm_pending_action(
    request: Request,
    action_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Run an agent write the user approved with their own click.

    The agent holds writes as pending once untrusted text has entered the
    conversation (app.domain.skill_safety). This endpoint is the only way to
    run one, and it runs as the approving user, with their permissions.
    """
    _ = request
    from app.domain.base_skill import SkillContext
    from app.domain.registry import run_pending_action

    record = _pending_or_404(action_id, current_user)
    conversation = None
    if record.get("conversation_id"):
        conversation = (
            db.query(Conversation)
            .filter(
                Conversation.id == record["conversation_id"],
                Conversation.user_id == current_user.id,
            )
            .first()
        )
    if conversation is None:
        raise HTTPException(404, "This approval has expired or was already used")

    _ = current_user.role
    context_manager = ContextManager(db=db, conversation=conversation, user=current_user)
    context = SkillContext(
        db=db,
        context_manager=context_manager,
        user_id=current_user.id,
        user_role=context_manager.user_role,
        conversation_id=conversation.id,
        user=current_user,
    )
    result = await run_pending_action(record, context)
    db.add(
        Message(
            conversation_id=conversation.id,
            role="assistant",
            content=(
                f"Approved `{record['skill_name']}`: {result.message}"
                if result.success
                else f"`{record['skill_name']}` failed: {result.message}"
            ),
            content_blocks=result.content_blocks or None,
            panel_payload=result.panel_payload,
        )
    )
    db.commit()
    return {
        "success": result.success,
        "skill": record["skill_name"],
        "message": result.message,
        "content_blocks": result.content_blocks,
        "panel_payload": result.panel_payload,
        "conversation_id": conversation.id,
    }


@router.post("/pending-actions/{action_id}/discard")
@limiter.limit("30/minute")
async def discard_pending_action(
    request: Request,
    action_id: str,
    current_user: User = Depends(get_current_user),
):
    _ = request
    record = _pending_or_404(action_id, current_user)
    return {"discarded": True, "skill": record["skill_name"]}
