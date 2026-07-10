"""Context Manager -- maintains conversation state, user context, and working memory."""

from typing import Any
from sqlalchemy.orm import Session
from app.models.conversation import Conversation, Message
from app.models.user import User


class ContextManager:
    """
    Manages all stateful context for a conversation:
    - Conversation history (for LLM context window)
    - User context (role, BU, permissions)
    - Working memory (active forecast version, last results, etc.)
    """

    def __init__(self, db: Session, conversation: Conversation, user: User):
        self.db = db
        self.conversation = conversation
        self.user = user
        self._working_memory: dict[str, Any] = conversation.working_memory or {}

    @property
    def user_id(self) -> str:
        return self.user.id

    @property
    def user_role(self) -> str:
        return self.user.role.name

    @property
    def user_business_unit(self) -> str | None:
        return self.user.business_unit

    @property
    def user_full_name(self) -> str:
        return self.user.full_name or self.user.username

    @property
    def conversation_id(self) -> str:
        return self.conversation.id

    # ---------- Working Memory ----------

    def get_memory(self, key: str, default: Any = None) -> Any:
        """Get a value from working memory."""
        return self._working_memory.get(key, default)

    def set_memory(self, key: str, value: Any) -> None:
        """Set a value in working memory and persist to DB."""
        self._working_memory[key] = value
        self.conversation.working_memory = self._working_memory
        self.db.commit()

    def get_active_version_id(self) -> str | None:
        """Get the currently active forecast version."""
        return self.get_memory("active_version_id")

    def set_active_version_id(self, version_id: str) -> None:
        """Set the active forecast version."""
        self.set_memory("active_version_id", version_id)

    # ---------- Conversation History ----------

    def get_chat_history(self, max_messages: int = 50, max_tokens: int = 8000) -> list[dict[str, str]]:
        """
        Get conversation history for the LLM context window.

        Uses a token budget (approx 4 chars/token): keep recent turns, and
        replace evicted older turns with a compact extractive summary so the
        model retains continuity without blowing the context window.
        """
        messages = (
            self.db.query(Message)
            .filter(Message.conversation_id == self.conversation_id)
            .order_by(Message.created_at.desc())
            .limit(max(max_messages * 2, 100))
            .all()
        )
        messages.reverse()

        history: list[dict[str, str]] = []
        for msg in messages:
            if msg.role in ("user", "assistant"):
                history.append({"role": msg.role, "content": msg.content or ""})

        budget = max_tokens
        kept: list[dict[str, str]] = []
        for msg in reversed(history):
            cost = max(1, len(msg["content"]) // 4)
            if budget - cost < 0 and kept:
                break
            kept.append(msg)
            budget -= cost
        kept.reverse()
        kept = kept[-max_messages:]

        # Summarize anything that fell outside the kept window
        if len(kept) < len(history):
            dropped = history[: len(history) - len(kept)]
            summary = self._summarize_evicted(dropped, max_chars=min(budget * 4, 2000))
            if summary:
                return [{"role": "assistant", "content": summary}, *kept]
        return kept

    @staticmethod
    def _summarize_evicted(messages: list[dict[str, str]], max_chars: int = 2000) -> str:
        """Extractive summary of dropped turns (no LLM call — deterministic)."""
        if not messages:
            return ""
        snippets: list[str] = []
        for msg in messages:
            role = "User" if msg["role"] == "user" else "Assistant"
            text = " ".join((msg.get("content") or "").split())
            if not text:
                continue
            if len(text) > 180:
                text = text[:177] + "..."
            snippets.append(f"{role}: {text}")
        body = " | ".join(snippets)
        header = (
            f"[Earlier conversation summary — {len(messages)} turn(s) condensed] "
        )
        room = max(max_chars - len(header), 80)
        if len(body) > room:
            body = body[: room - 3] + "..."
        return header + body

    def get_system_context(self) -> str:
        """Build the system prompt with user context and working memory."""
        context_parts = [
            f"User: {self.user_full_name} (role: {self.user_role})",
        ]
        if self.user_business_unit:
            context_parts.append(f"Business Unit: {self.user_business_unit}")

        active_version = self.get_active_version_id()
        if active_version:
            context_parts.append(f"Active Forecast Version: {active_version}")

        last_dataset = self.get_memory("last_dataset_id")
        if last_dataset:
            context_parts.append(f"Last Loaded Dataset: {last_dataset}")

        return "\n".join(context_parts)

    # ---------- Document Context (RAG) ----------

    def get_relevant_context(self, query: str, top_k: int = 3) -> str:
        """Auto-retrieve relevant chunks from the context engine for the system prompt."""
        try:
            from app.services import context_engine

            return context_engine.get_context_for_query(
                db=self.db,
                query=query,
                user_id=self.user_id,
                conversation_id=self.conversation_id,
                top_k=top_k,
            )
        except Exception:
            return ""

    # ---------- Permission Checks ----------

    def has_permission(self, permission: str) -> bool:
        """Check if the current user has a specific permission."""
        role = self.user.role
        permission_map = {
            "input": role.can_input,
            "generate": role.can_generate,
            "override": role.can_override,
            "review": role.can_review,
            "publish": role.can_publish,
            "admin": role.can_admin,
        }
        return permission_map.get(permission, False)
