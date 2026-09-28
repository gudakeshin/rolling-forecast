"""Sub-Agent Coordinator -- manages skill execution lifecycle."""

import logging
from typing import Any, AsyncIterator

from app.domain.registry import get_registry
from app.domain.base_skill import SkillContext, SkillResult
from app.orchestration.task_planner import TaskPlan

logger = logging.getLogger(__name__)


class SubAgentCoordinator:
    """
    Executes task plans by invoking skills in the correct order.
    
    Handles:
    - Sequential and parallel step execution
    - Progress streaming
    - Error handling and graceful degradation
    - Result aggregation
    """

    def __init__(self, context: SkillContext):
        self.context = context
        self.registry = get_registry()

    async def execute_plan(
        self, plan: TaskPlan
    ) -> AsyncIterator[dict[str, Any]]:
        """
        Execute a task plan step by step, yielding progress events.
        
        Yields:
            Dict events: {type: "progress"|"step_complete"|"plan_complete"|"error", ...}
        """
        total_steps = len(plan.steps)
        completed_results: dict[str, SkillResult] = {}

        yield {
            "type": "progress",
            "data": {
                "message": f"Starting workflow: {plan.intent}",
                "progress": 0,
                "total_steps": total_steps,
            },
        }

        for i, step in enumerate(plan.steps):
            # Check dependencies
            for dep in step.dependencies:
                if dep not in completed_results:
                    yield {
                        "type": "error",
                        "data": {
                            "message": f"Dependency '{dep}' not yet completed for step '{step.skill_name}'",
                        },
                    }
                    return

            skill = self.registry.get(step.skill_name)
            if skill is None:
                yield {
                    "type": "error",
                    "data": {"message": f"Skill '{step.skill_name}' not found"},
                }
                return

            yield {
                "type": "progress",
                "data": {
                    "message": step.description,
                    "progress": i / total_steps,
                    "current_step": i + 1,
                    "total_steps": total_steps,
                    "skill_name": step.skill_name,
                },
            }

            try:
                result = await skill.execute(step.parameters, self.context)
                completed_results[step.skill_name] = result

                yield {
                    "type": "step_complete",
                    "data": {
                        "skill_name": step.skill_name,
                        "success": result.success,
                        "message": result.message,
                        "content_blocks": result.content_blocks,
                        "panel_payload": result.panel_payload,
                    },
                }

                if not result.success:
                    yield {
                        "type": "error",
                        "data": {
                            "message": f"Step '{step.skill_name}' failed: {result.message}",
                            "error": result.error,
                        },
                    }
                    return

            except Exception as e:
                logger.error(f"Step '{step.skill_name}' raised exception: {e}", exc_info=True)
                yield {
                    "type": "error",
                    "data": {
                        "message": f"Step '{step.skill_name}' failed with error: {str(e)}",
                    },
                }
                return

        # All steps completed
        yield {
            "type": "plan_complete",
            "data": {
                "message": f"Workflow '{plan.intent}' completed successfully",
                "steps_completed": total_steps,
                "results": {
                    name: {"success": r.success, "message": r.message}
                    for name, r in completed_results.items()
                },
            },
        }

    async def execute_single_skill(
        self, skill_name: str, params: dict[str, Any]
    ) -> SkillResult:
        """Execute a single skill directly."""
        skill = self.registry.get(skill_name)
        if skill is None:
            return SkillResult.fail(f"Skill '{skill_name}' not found")

        if not skill.validate_permissions(self.context):
            return SkillResult.fail(
                f"Permission denied: role '{self.context.user_role}' cannot use '{skill_name}'"
            )

        return await skill.execute(params, self.context)
