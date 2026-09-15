"""run_what_if skill — driver shock scenarios (Phase 7 / U2)."""

from __future__ import annotations

import logging
from typing import Any

from app.domain.base_skill import BaseSkill, SkillContext, SkillResult
from app.services.permissions import resolve_skill_user, user_has_permission
from app.services.scenario_what_if import DriverShock, create_what_if_scenario

logger = logging.getLogger(__name__)


class RunWhatIfSkill(BaseSkill):
    @property
    def name(self) -> str:
        return "run_what_if"

    @property
    def description(self) -> str:
        return (
            "Create a what-if forecast scenario by shocking causal drivers "
            "(e.g. 'what if headcount grows 10% slower'). Clones the base version, "
            "persists scenario driver values, and perturbs linked line items. "
            "Use when the user asks for scenarios, sensitivity, or driver shocks."
        )

    @property
    def required_role(self) -> str:
        return "generate"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "base_version_id": {
                    "type": "string",
                    "description": "Base forecast version (defaults to active)",
                },
                "scenario_label": {
                    "type": "string",
                    "description": "Label for the new scenario version",
                },
                "shocks": {
                    "type": "array",
                    "description": (
                        "List of {driver_id|driver_key, mode, value}. "
                        "mode is pct|absolute|replace."
                    ),
                    "items": {"type": "object"},
                },
                "open_panel": {"type": "boolean", "default": True},
            },
            "required": ["scenario_label", "shocks"],
        }

    async def execute(self, params: dict[str, Any], context: SkillContext) -> SkillResult:
        user = resolve_skill_user(context)
        if user is None:
            return SkillResult.fail("Authenticated user required.")
        if not user_has_permission(user, "generate"):
            return SkillResult.fail("Permission generate required.")

        base_version_id = (
            params.get("base_version_id")
            or context.context_manager.get_active_version_id()
        )
        if not base_version_id:
            return SkillResult.fail("No base forecast version. Select or generate one first.")

        label = (params.get("scenario_label") or "").strip()
        if not label:
            return SkillResult.fail("scenario_label is required.")

        raw_shocks = params.get("shocks") or []
        if not isinstance(raw_shocks, list) or not raw_shocks:
            return SkillResult.fail("At least one shock is required.")

        shocks: list[DriverShock] = []
        for raw in raw_shocks:
            if not isinstance(raw, dict):
                return SkillResult.fail("Each shock must be an object.")
            driver_id = raw.get("driver_id")
            if driver_id is None and raw.get("driver_key"):
                from app.models.driver import Driver

                d = (
                    context.db.query(Driver)
                    .filter(Driver.key == str(raw["driver_key"]).strip())
                    .first()
                )
                if not d:
                    return SkillResult.fail(f"Driver key '{raw['driver_key']}' not found.")
                driver_id = d.id
            if driver_id is None:
                return SkillResult.fail("Each shock needs driver_id or driver_key.")
            mode = str(raw.get("mode") or "pct")
            shocks.append(
                DriverShock(
                    driver_id=int(driver_id),
                    mode=mode,
                    value=raw.get("value"),
                    series=raw.get("series"),
                )
            )

        try:
            result = create_what_if_scenario(
                context.db,
                base_version_id=base_version_id,
                scenario_label=label,
                shocks=shocks,
                actor=user,
            )
        except ValueError as e:
            return SkillResult.fail(str(e))

        context.context_manager.set_active_version_id(result["scenario_version_id"])

        panel = None
        if params.get("open_panel", True):
            panel = {
                "panel": "what_if",
                "params": {"version_id": result["scenario_version_id"]},
                "title": "What-if Scenario",
            }

        msg = (
            f"Created scenario **{result.get('scenario_label', label)}** "
            f"(`{result['scenario_version_id']}`): "
            f"{result.get('affected_line_periods', 0)} line-periods perturbed across "
            f"{result.get('affected_line_items', 0)} line items."
        )
        return SkillResult.ok(
            message=msg,
            data=result,
            content_blocks=[self._text_block(msg)],
            panel_payload=panel,
        )
