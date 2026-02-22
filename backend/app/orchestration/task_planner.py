"""Task Planner -- decomposes complex user requests into ordered sub-tasks."""

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class TaskStep:
    """A single step in a task plan."""
    skill_name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=dict)
    dependencies: list[str] = field(default_factory=list)  # skill names that must complete first
    is_parallel: bool = False  # Can run in parallel with other steps


@dataclass
class TaskPlan:
    """An ordered plan of sub-tasks to accomplish a user request."""
    intent: str
    steps: list[TaskStep] = field(default_factory=list)
    estimated_duration: str = "unknown"


class TaskPlanner:
    """
    Decomposes complex multi-step user requests into ordered task plans.
    
    For V1, this uses pattern matching on common intents.
    Future versions could use Claude to generate plans dynamically.
    """

    # Common multi-step patterns
    PATTERNS: dict[str, list[dict]] = {
        "full_refresh": [
            {"skill": "ingest_actuals", "desc": "Load latest actuals data"},
            {"skill": "generate_baseline", "desc": "Generate statistical baseline forecast"},
            {"skill": "score_confidence", "desc": "Score confidence and flag review items"},
        ],
        "review_cycle": [
            {"skill": "score_confidence", "desc": "Identify items needing review"},
            {"skill": "query_forecast", "desc": "Summarize review queue"},
        ],
        "compare_and_analyze": [
            {"skill": "compare_forecasts", "desc": "Compare current vs prior forecast"},
            {"skill": "query_forecast", "desc": "Analyze key variances"},
        ],
    }

    def plan(self, intent: str, context: dict[str, Any] = None) -> TaskPlan | None:
        """
        Create a task plan for a recognized multi-step intent.
        
        Returns None if the intent doesn't require multi-step planning
        (i.e., it can be handled by a single skill invocation by the agent).
        """
        context = context or {}

        # Check for known patterns
        for pattern_name, steps in self.PATTERNS.items():
            if pattern_name in intent.lower().replace(" ", "_"):
                task_steps = [
                    TaskStep(
                        skill_name=s["skill"],
                        description=s["desc"],
                        parameters=context,
                    )
                    for s in steps
                ]
                return TaskPlan(
                    intent=intent,
                    steps=task_steps,
                    estimated_duration=f"{len(task_steps) * 30}s",
                )

        return None
