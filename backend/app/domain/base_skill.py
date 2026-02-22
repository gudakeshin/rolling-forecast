"""Base skill interface -- all domain skills implement this contract.

Skills are defined by editable .md files in the backend/skills/ directory.
Each .md file contains YAML frontmatter with the skill's metadata (name,
description, parameters, required_role, tags) and a markdown body with
instructions, behavior notes, and examples for the LLM.

The .md files are the source of truth for how the LLM discovers and invokes
skills. The Python skill classes contain the execution logic. The registry
merges both at startup: .md metadata for the LLM-facing tool description,
Python class for the actual execution.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any
from sqlalchemy.orm import Session
from app.orchestration.context_manager import ContextManager


@dataclass
class SkillContext:
    """Context passed to every skill execution."""
    db: Session
    context_manager: ContextManager
    user_id: str
    user_role: str
    conversation_id: str
    working_memory: dict[str, Any] = field(default_factory=dict)


@dataclass
class SkillResult:
    """Structured result from skill execution."""
    success: bool
    message: str  # Human-readable summary for chat
    data: dict[str, Any] = field(default_factory=dict)  # Structured data
    content_blocks: list[dict[str, Any]] = field(default_factory=list)  # Rich chat content
    panel_payload: dict[str, Any] | None = None  # Side panel trigger data
    error: str | None = None

    @staticmethod
    def ok(message: str, data: dict = None, content_blocks: list = None,
           panel_payload: dict = None) -> "SkillResult":
        return SkillResult(
            success=True,
            message=message,
            data=data or {},
            content_blocks=content_blocks or [],
            panel_payload=panel_payload,
        )

    @staticmethod
    def fail(message: str, error: str = None) -> "SkillResult":
        return SkillResult(
            success=False,
            message=message,
            error=error or message,
        )


class BaseSkill(ABC):
    """
    Abstract base class for all domain skills.
    
    Each skill represents a discrete capability that the agent can invoke.
    Skills are registered in the SkillsRegistry and exposed to the LLM
    as callable tools via LangChain.

    Skill metadata (name, description, parameters) can be customized by
    editing the corresponding .md file in backend/skills/<skill_name>.md.
    The registry merges the .md definition with the Python execution class.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique skill identifier (e.g., 'ingest_actuals')."""
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """Natural language description for the LLM to understand when to use this skill."""
        ...

    @property
    @abstractmethod
    def parameters_schema(self) -> dict:
        """
        JSON Schema describing the input parameters.
        Used by LangChain to generate the tool's argument schema.
        Example:
        {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Path to CSV file"},
            },
            "required": ["file_path"]
        }
        """
        ...

    @property
    def required_role(self) -> str | None:
        """Minimum role required to invoke this skill. None = any authenticated user."""
        return None

    @abstractmethod
    async def execute(self, params: dict[str, Any], context: SkillContext) -> SkillResult:
        """
        Execute the skill logic.
        
        Args:
            params: Validated input parameters matching parameters_schema
            context: Execution context with DB session, user info, working memory
            
        Returns:
            SkillResult with structured output for chat display and optional panel data
        """
        ...

    def validate_permissions(self, context: SkillContext) -> bool:
        """Check if the user has permission to run this skill."""
        if self.required_role is None:
            return True
        return context.context_manager.has_permission(self.required_role)

    def _text_block(self, text: str) -> dict:
        """Helper to create a text content block."""
        return {"type": "text", "data": {"text": text}}

    def _table_block(self, title: str, columns: list[dict], rows: list[dict]) -> dict:
        """Helper to create a table content block."""
        return {
            "type": "table",
            "data": {"title": title, "columns": columns, "rows": rows},
        }

    def _status_block(self, label: str, progress: float = 0, step: str = "",
                      is_complete: bool = False) -> dict:
        """Helper to create a status/progress content block."""
        return {
            "type": "status",
            "data": {
                "label": label,
                "progress": progress,
                "step": step,
                "is_complete": is_complete,
            },
        }

    def _panel_trigger(self, panel: str, params: dict, label: str = "View Details") -> dict:
        """Helper to create a panel trigger content block."""
        return {
            "type": "panel_trigger",
            "data": {"panel": panel, "params": params, "label": label},
        }

    def _action_block(self, actions: list[dict]) -> dict:
        """Helper to create an action button content block."""
        return {"type": "action", "data": {"actions": actions}}
