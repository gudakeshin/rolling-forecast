"""Skill Composer -- chains multiple skills into reusable workflows."""

import logging
from dataclasses import dataclass, field
from typing import Any

from app.domain.base_skill import BaseSkill, SkillContext, SkillResult
from app.domain.registry import get_registry

logger = logging.getLogger(__name__)


@dataclass
class SkillStep:
    """A single step in a composed skill workflow."""
    skill_name: str
    params: dict[str, Any] = field(default_factory=dict)
    # Map output keys from previous step to input params of this step
    input_mapping: dict[str, str] = field(default_factory=dict)
    # Whether to continue the workflow on failure of this step
    continue_on_failure: bool = False


class ComposedSkill(BaseSkill):
    """
    A skill composed of multiple sequential skill steps.
    
    Each step can reference outputs from previous steps via input_mapping.
    The composed skill aggregates all results and content blocks.
    """

    def __init__(
        self,
        skill_name: str,
        skill_description: str,
        steps: list[SkillStep],
        params_schema: dict | None = None,
    ):
        self._name = skill_name
        self._description = skill_description
        self._steps = steps
        self._params_schema = params_schema or {"type": "object", "properties": {}}

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def parameters_schema(self) -> dict:
        return self._params_schema

    async def execute(self, params: dict[str, Any], context: SkillContext) -> SkillResult:
        """Execute all steps in sequence, threading data between them."""
        registry = get_registry()
        all_content_blocks = []
        all_data = {}
        step_outputs: dict[str, dict] = {}  # skill_name -> output data

        for i, step in enumerate(self._steps):
            skill = registry.get(step.skill_name)
            if skill is None:
                return SkillResult.fail(f"Skill '{step.skill_name}' not found in registry")

            # Build params: start with step params, then overlay mapped inputs
            step_params = {**step.params, **params}
            for target_key, source_path in step.input_mapping.items():
                # source_path format: "skill_name.key"
                parts = source_path.split(".", 1)
                if len(parts) == 2 and parts[0] in step_outputs:
                    step_params[target_key] = step_outputs[parts[0]].get(parts[1])

            logger.info(f"Composed skill '{self._name}' executing step {i+1}/{len(self._steps)}: {step.skill_name}")

            # Execute the step
            result = await skill.execute(step_params, context)
            step_outputs[step.skill_name] = result.data
            all_content_blocks.extend(result.content_blocks)
            all_data[step.skill_name] = result.data

            if not result.success and not step.continue_on_failure:
                return SkillResult.fail(
                    f"Workflow '{self._name}' failed at step '{step.skill_name}': {result.message}",
                    error=result.error,
                )

        # Return aggregated result
        return SkillResult.ok(
            message=f"Workflow '{self._name}' completed successfully ({len(self._steps)} steps)",
            data=all_data,
            content_blocks=all_content_blocks,
            panel_payload=None,
        )


class SkillComposer:
    """Factory for creating composed skill workflows."""

    @staticmethod
    def create_forecast_refresh_workflow() -> ComposedSkill:
        """Create the standard forecast refresh workflow."""
        return ComposedSkill(
            skill_name="forecast_refresh",
            skill_description=(
                "Execute a full forecast refresh workflow: ingest latest actuals, "
                "generate statistical baseline, score confidence, and create a versioned snapshot. "
                "Use this when the user wants to refresh or regenerate the forecast."
            ),
            steps=[
                SkillStep(
                    skill_name="ingest_actuals",
                    params={},
                ),
                SkillStep(
                    skill_name="generate_baseline",
                    params={},
                    input_mapping={"dataset_id": "ingest_actuals.dataset_id"},
                ),
                SkillStep(
                    skill_name="score_confidence",
                    params={},
                    input_mapping={"version_id": "generate_baseline.version_id"},
                ),
            ],
            params_schema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path to actuals CSV file (optional if already uploaded)",
                    },
                    "horizon_months": {
                        "type": "integer",
                        "description": "Forecast horizon in months (default: 12)",
                        "default": 12,
                    },
                },
            },
        )
