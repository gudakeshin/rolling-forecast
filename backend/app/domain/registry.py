"""Skills Registry -- central registry with .md definition merging and LangChain bridge.

Skills are composed of two parts:
1. An editable .md file (in backend/skills/) defining metadata, description,
   parameters, tags, and LLM instructions.
2. A Python class (in app/domain/skills/) containing the execution logic.

The registry merges both at startup. When converting to LangChain tools,
the .md definition's description (including "When to Use" and "Examples")
is used for the LLM-facing tool description, providing richer context for
the agent's tool selection.

Users can edit .md files to customize skill behavior without touching Python.
"""

import json
import logging
from typing import Any, Optional
from pydantic import BaseModel, Field, create_model
from langchain_core.tools import StructuredTool

from app.domain.base_skill import BaseSkill, SkillContext, SkillResult
from app.domain.skill_loader import (
    SkillDefinition,
    load_all_skill_definitions,
    load_skill_definition,
)

logger = logging.getLogger(__name__)

# JSON Schema type -> Python type mapping
_TYPE_MAP = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "object": dict,
    "array": list,
}


def _build_args_model(skill_name: str, parameters_schema: dict) -> type[BaseModel]:
    """Dynamically create a Pydantic model from a skill's JSON Schema parameters.

    This ensures LangChain StructuredTool has a proper args_schema so the LLM's
    structured arguments are correctly routed to the skill's execute() method.
    """
    properties = parameters_schema.get("properties", {})
    required_fields = set(parameters_schema.get("required", []))

    if not properties:
        # No parameters — create a model with no fields
        return create_model(f"{skill_name}_Args")

    field_definitions: dict[str, Any] = {}
    for field_name, field_spec in properties.items():
        field_type_str = field_spec.get("type", "string")
        py_type = _TYPE_MAP.get(field_type_str, str)
        description = field_spec.get("description", "")
        default = field_spec.get("default", ...)

        if field_name in required_fields:
            # Required field
            field_definitions[field_name] = (
                py_type,
                Field(description=description),
            )
        else:
            # Optional field with default
            if default is ...:
                # Optional with no default → default to None
                field_definitions[field_name] = (
                    Optional[py_type],
                    Field(default=None, description=description),
                )
            else:
                field_definitions[field_name] = (
                    Optional[py_type],
                    Field(default=default, description=description),
                )

    model_name = f"{skill_name}_Args"
    return create_model(model_name, **field_definitions)


class SkillsRegistry:
    """
    Central registry where all skills are registered at startup.
    
    The master agent queries the registry to discover available tools.
    Skills are automatically wrapped as LangChain tools for the ReACT agent.
    .md definitions enrich the tool descriptions with detailed instructions.
    """

    def __init__(self):
        self._skills: dict[str, BaseSkill] = {}
        self._definitions: dict[str, SkillDefinition] = {}
        # Bumped on .md reload/edit so MasterAgent can invalidate its graph cache
        self.definitions_generation: int = 0

    def register(self, skill: BaseSkill) -> None:
        """Register a skill in the registry and load its .md definition."""
        if skill.name in self._skills:
            logger.warning(f"Skill '{skill.name}' already registered, overwriting.")
        self._skills[skill.name] = skill

        # Try to load the .md definition
        defn = load_skill_definition(skill.name)
        if defn:
            self._definitions[skill.name] = defn
            logger.info(f"Registered skill: {skill.name} (with .md definition v{defn.version})")
        else:
            logger.info(f"Registered skill: {skill.name} (no .md definition found)")

    def bump_definitions_generation(self) -> None:
        self.definitions_generation += 1
        try:
            from app.orchestration.master_agent import invalidate_agent_cache
            invalidate_agent_cache()
        except Exception:
            pass

    def load_definitions(self) -> None:
        """Load/reload all .md definitions from the skills directory."""
        self._definitions = load_all_skill_definitions()
        self.bump_definitions_generation()
        logger.info(f"Loaded {len(self._definitions)} skill definitions from .md files")

    def get(self, name: str) -> BaseSkill | None:
        """Retrieve a skill by name."""
        return self._skills.get(name)

    def get_definition(self, name: str) -> SkillDefinition | None:
        """Retrieve the .md definition for a skill."""
        return self._definitions.get(name)

    def list_all(self) -> list[BaseSkill]:
        """List all registered skills."""
        return list(self._skills.values())

    def list_names(self) -> list[str]:
        """List all registered skill names."""
        return list(self._skills.keys())

    def list_with_definitions(self) -> list[dict[str, Any]]:
        """List all skills with their .md definition metadata."""
        results = []
        for name, skill in self._skills.items():
            defn = self._definitions.get(name)
            results.append({
                "name": name,
                "description": defn.description[:120] if defn else skill.description[:120],
                "has_definition": defn is not None,
                "tags": defn.tags if defn else [],
                "version": defn.version if defn else "1.0",
                "required_role": defn.required_role if defn else skill.required_role,
                "param_count": len(defn.parameters) if defn else len(
                    skill.parameters_schema.get("properties", {})
                ),
            })
        return results

    def get_for_role(self, role: str) -> list[BaseSkill]:
        """Get skills available for a given role permission flag or role name.

        ``role`` may be a permission key (generate/override/review/...) or a
        role name (admin/analyst/reviewer/...). Admin gets all skills.
        """
        role_permissions = {
            "admin": {"input", "generate", "override", "review", "publish", "admin"},
            "analyst": {"input", "generate", "override"},
            "reviewer": {"input", "generate", "override", "review"},
            "publisher": {"input", "generate", "override", "review", "publish"},
            "input_provider": {"input"},
            "manager": {"input", "generate", "override", "review"},  # alias
        }
        # Direct permission key
        if role in {"input", "generate", "override", "review", "publish", "admin"}:
            allowed = {role, "input"} if role != "admin" else role_permissions["admin"]
            if role == "admin":
                return self.list_all()
        else:
            allowed = role_permissions.get(role, {"input"})

        if "admin" in allowed:
            return self.list_all()

        result = []
        for skill in self._skills.values():
            required = skill.required_role or "generate"
            if required in allowed or required == "input":
                result.append(skill)
        return result

    def as_langchain_tools(self, context: SkillContext | None = None) -> list[StructuredTool]:
        """
        Convert role-filtered skills into LangChain tools.

        Tools resolve SkillContext at invoke time via ContextVar so the compiled
        agent graph can be cached across turns. ``context`` is only used to pick
        which skills to expose for the caller's role.
        """
        from app.domain.base_skill import get_active_skill_context

        role_key = "analyst"
        if context is not None:
            role_name = getattr(getattr(context, "user", None), "role", None)
            role_key = role_name.name if role_name else getattr(context, "user_role", "analyst")
        skills = self.get_for_role(role_key)
        tools = []
        for skill in skills:
            tool = self._skill_to_tool(skill, get_active_skill_context)
            tools.append(tool)
        return tools

    def _skill_to_tool(self, skill: BaseSkill, get_context) -> StructuredTool:
        """Convert a single skill to a LangChain StructuredTool.
        
        Uses the .md definition's enriched description if available.
        Dynamically creates a Pydantic args_schema so the LLM's structured
        arguments are correctly routed to the skill's execute() method.
        """
        defn = self._definitions.get(skill.name)

        # Use .md enriched description if available (includes When to Use + Examples)
        if defn:
            tool_description = defn.full_description
        else:
            tool_description = skill.description

        # Truncate description to stay within LLM tool description limits
        if len(tool_description) > 1024:
            tool_description = tool_description[:1020] + "..."

        # Build a proper Pydantic args schema so LangChain routes params correctly
        args_model = _build_args_model(skill.name, skill.parameters_schema)

        async def _invoke_skill(**kwargs) -> str:
            """Wrapper that invokes the skill and returns formatted output."""
            context = get_context()
            if context is None:
                return json.dumps({
                    "success": False,
                    "error": "No active skill context for this request",
                })
            # Check permissions
            if not skill.validate_permissions(context):
                return json.dumps({
                    "success": False,
                    "error": f"Permission denied: role '{context.user_role}' cannot use '{skill.name}'",
                })

            try:
                result: SkillResult = await skill.execute(kwargs, context)
                return json.dumps({
                    "success": result.success,
                    "message": result.message,
                    "data": result.data,
                    "content_blocks": result.content_blocks,
                    "panel_payload": result.panel_payload,
                    "error": result.error,
                })
            except Exception as e:
                logger.error(f"Skill '{skill.name}' execution failed: {e}", exc_info=True)
                return json.dumps({
                    "success": False,
                    "error": f"Skill execution failed: {str(e)}",
                })

        return StructuredTool.from_function(
            coroutine=_invoke_skill,
            name=skill.name,
            description=tool_description,
            args_schema=args_model,
            return_direct=False,
        )


# Global registry singleton
_registry: SkillsRegistry | None = None


def get_registry() -> SkillsRegistry:
    """Get the global skills registry singleton."""
    global _registry
    if _registry is None:
        _registry = SkillsRegistry()
    return _registry


def register_all_skills() -> SkillsRegistry:
    """Register all available skills. Called at application startup."""
    registry = get_registry()

    # Load all .md definitions first
    registry.load_definitions()

    # Phase 1 skills
    from app.domain.skills.ingest_actuals import IngestActualsSkill
    from app.domain.skills.plan_forecast import PlanForecastSkill
    from app.domain.skills.generate_baseline import GenerateBaselineSkill
    from app.domain.skills.score_confidence import ScoreConfidenceSkill
    from app.domain.skills.manage_versions import ManageVersionsSkill
    from app.domain.skills.query_forecast import QueryForecastSkill

    registry.register(IngestActualsSkill())
    registry.register(PlanForecastSkill())
    registry.register(GenerateBaselineSkill())
    registry.register(ScoreConfidenceSkill())
    registry.register(ManageVersionsSkill())
    registry.register(QueryForecastSkill())

    # Phase 2 skills
    from app.domain.skills.apply_override import ApplyOverrideSkill
    from app.domain.skills.compare_forecasts import CompareForecastsSkill
    from app.domain.skills.collect_driver_input import CollectDriverInputSkill
    from app.domain.skills.review_forecast import ReviewForecastSkill

    registry.register(ApplyOverrideSkill())
    registry.register(CompareForecastsSkill())
    registry.register(CollectDriverInputSkill())
    registry.register(ReviewForecastSkill())

    # Phase 3 skills
    from app.domain.skills.export_audit import ExportAuditSkill
    registry.register(ExportAuditSkill())

    # Phase 4 skills
    from app.domain.skills.run_ensemble import RunEnsembleSkill
    from app.domain.skills.generate_commentary import GenerateCommentarySkill
    from app.domain.skills.branch_forecast import BranchForecastSkill
    from app.domain.skills.auto_accuracy_report import AutoAccuracyReportSkill
    from app.domain.skills.detect_anomalies import DetectAnomaliesSkill

    registry.register(RunEnsembleSkill())
    registry.register(GenerateCommentarySkill())
    registry.register(BranchForecastSkill())
    registry.register(AutoAccuracyReportSkill())
    registry.register(DetectAnomaliesSkill())

    # Context Engine skills
    from app.domain.skills.search_context import SearchContextSkill
    from app.domain.skills.web_search import WebSearchSkill
    from app.domain.skills.fetch_url import FetchURLSkill
    from app.domain.skills.financial_lookup import FinancialLookupSkill

    registry.register(SearchContextSkill())
    registry.register(WebSearchSkill())
    registry.register(FetchURLSkill())
    registry.register(FinancialLookupSkill())

    # U2 — driver surfaces / attribution / what-if
    from app.domain.skills.manage_drivers import ManageDriversSkill
    from app.domain.skills.explain_variance import ExplainVarianceSkill
    from app.domain.skills.run_what_if import RunWhatIfSkill
    from app.domain.skills.core_memory import (
        CoreMemoryAppendSkill,
        CoreMemoryReplaceSkill,
        CoreMemoryListSkill,
        ConversationSearchSkill,
    )

    registry.register(ManageDriversSkill())
    registry.register(ExplainVarianceSkill())
    registry.register(RunWhatIfSkill())
    registry.register(CoreMemoryAppendSkill())
    registry.register(CoreMemoryReplaceSkill())
    registry.register(CoreMemoryListSkill())
    registry.register(ConversationSearchSkill())

    logger.info(f"Registered {len(registry.list_names())} skills: {registry.list_names()}")
    return registry
