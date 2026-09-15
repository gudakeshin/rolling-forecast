"""list_model_presets / manage_model_presets — chat access to saved model presets.

Split into two skill classes (mirroring app/domain/skills/core_memory.py) rather
than one action-dispatch skill, because required_role is enforced once per whole
skill (see SkillsRegistry.list_for_role): any analyst should be able to browse
presets to run a forecast with one, but only admins should create/edit/retire
them — the same split the REST layer (app/api/model_presets.py) enforces.
"""

from __future__ import annotations

import logging
from typing import Any

from app.domain.base_skill import BaseSkill, SkillContext, SkillResult
from app.services.model_presets import (
    create_preset,
    deactivate_preset,
    find_preset,
    list_presets,
    update_preset,
)
from app.services.permissions import resolve_skill_user

logger = logging.getLogger(__name__)


def _serialize(preset) -> dict:
    return {
        "id": preset.id,
        "name": preset.name,
        "description": preset.description,
        "model_type": preset.model_type,
        "candidate_models": preset.candidate_models,
        "default_horizon_months": preset.default_horizon_months,
        "is_active": preset.is_active,
    }


class ListModelPresetsSkill(BaseSkill):
    """Read-only: any analyst can see what model presets exist."""

    @property
    def name(self) -> str:
        return "list_model_presets"

    @property
    def description(self) -> str:
        return (
            "List saved model presets (named shortcuts for a model_type/"
            "models_to_test combination usable with generate_baseline's "
            "model_preset parameter), or look up one by name/id. Use when the "
            "user asks what models/presets are available, or before running a "
            "forecast with a named preset."
        )

    @property
    def required_role(self) -> str:
        return "generate"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "preset": {
                    "type": "string",
                    "description": "Name or id of a specific preset to look up. Omit to list all.",
                },
                "include_inactive": {
                    "type": "boolean",
                    "description": "Include deactivated presets in the list.",
                    "default": False,
                },
            },
        }

    async def execute(self, params: dict[str, Any], context: SkillContext) -> SkillResult:
        db = context.db
        preset_ref = params.get("preset")

        if preset_ref:
            preset = find_preset(db, preset_ref)
            if preset is None:
                return SkillResult.fail(f"Model preset '{preset_ref}' not found.")
            return SkillResult.ok(
                message=f"Model preset '{preset.name}': model_type={preset.model_type}",
                data={"preset": _serialize(preset)},
                content_blocks=[
                    self._table_block(
                        title=f"Model Preset: {preset.name}",
                        columns=[
                            {"key": "field", "label": "Field"},
                            {"key": "value", "label": "Value"},
                        ],
                        rows=[
                            {"field": "Description", "value": preset.description or "—"},
                            {"field": "Model type", "value": preset.model_type},
                            {
                                "field": "Candidate models",
                                "value": ", ".join(preset.candidate_models)
                                if preset.candidate_models
                                else "— (no restriction)",
                            },
                            {
                                "field": "Default horizon",
                                "value": f"{preset.default_horizon_months} months"
                                if preset.default_horizon_months
                                else "— (uses run default)",
                            },
                            {"field": "Active", "value": "Yes" if preset.is_active else "No"},
                        ],
                    )
                ],
            )

        rows = list_presets(db, include_inactive=bool(params.get("include_inactive")))
        if not rows:
            return SkillResult.ok(
                message="No model presets saved yet. Create one with manage_model_presets.",
                data={"presets": []},
            )
        return SkillResult.ok(
            message=f"Found {len(rows)} model preset(s).",
            data={"presets": [_serialize(r) for r in rows]},
            content_blocks=[
                self._table_block(
                    title="Model Presets",
                    columns=[
                        {"key": "name", "label": "Name"},
                        {"key": "model_type", "label": "Model type"},
                        {"key": "active", "label": "Active"},
                    ],
                    rows=[
                        {
                            "name": r.name,
                            "model_type": r.model_type,
                            "active": "Yes" if r.is_active else "No",
                        }
                        for r in rows
                    ],
                )
            ],
        )


class ManageModelPresetsSkill(BaseSkill):
    """Mutating: create, update, or deactivate a saved model preset. Admin only."""

    @property
    def name(self) -> str:
        return "manage_model_presets"

    @property
    def description(self) -> str:
        return (
            "Create, update, or deactivate a saved model preset — a named "
            "shortcut pinning a specific forecasting algorithm (or restricting "
            "the auto-selection candidate pool) for use with generate_baseline's "
            "model_preset parameter. Use when the user wants to save a model "
            "configuration for reuse, e.g. 'create a model preset called "
            "Conservative using ETS'."
        )

    @property
    def required_role(self) -> str:
        return "admin"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["create", "update", "deactivate"],
                    "description": "Action to perform",
                },
                "preset_id": {
                    "type": "string",
                    "description": "Preset id (required for update/deactivate)",
                },
                "name": {"type": "string", "description": "Preset name (create, or rename on update)"},
                "description": {"type": "string"},
                "model_type": {
                    "type": "string",
                    "description": "'auto' or a specific registry model name to pin for all line items",
                },
                "candidate_models": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Restrict the auto-selection pool (only when model_type='auto')",
                },
                "default_horizon_months": {"type": "integer"},
            },
            "required": ["action"],
        }

    async def execute(self, params: dict[str, Any], context: SkillContext) -> SkillResult:
        db = context.db
        action = params.get("action")
        actor = resolve_skill_user(context)

        try:
            if action == "create":
                preset = create_preset(
                    db,
                    name=params.get("name") or "",
                    description=params.get("description"),
                    model_type=params.get("model_type", "auto"),
                    candidate_models=params.get("candidate_models"),
                    default_horizon_months=params.get("default_horizon_months"),
                    actor=actor,
                )
                return SkillResult.ok(
                    message=f"Created model preset '{preset.name}' (model_type={preset.model_type}).",
                    data={"preset": _serialize(preset)},
                    content_blocks=[
                        self._text_block(
                            f"Created model preset **{preset.name}** (`model_type={preset.model_type}`). "
                            f"Use it via generate_baseline's `model_preset` parameter."
                        )
                    ],
                )
            elif action == "update":
                preset_id = params.get("preset_id")
                if not preset_id:
                    return SkillResult.fail("preset_id is required for update.")
                fields = {
                    k: params[k]
                    for k in (
                        "name",
                        "description",
                        "model_type",
                        "candidate_models",
                        "default_horizon_months",
                    )
                    if k in params
                }
                preset = update_preset(db, preset_id, actor=actor, **fields)
                return SkillResult.ok(
                    message=f"Updated model preset '{preset.name}'.",
                    data={"preset": _serialize(preset)},
                )
            elif action == "deactivate":
                preset_id = params.get("preset_id")
                if not preset_id:
                    return SkillResult.fail("preset_id is required for deactivate.")
                preset = deactivate_preset(db, preset_id, actor=actor)
                return SkillResult.ok(
                    message=f"Deactivated model preset '{preset.name}'.",
                    data={"preset": _serialize(preset)},
                )
            else:
                return SkillResult.fail(f"Unknown action: {action}")
        except ValueError as e:
            return SkillResult.fail(str(e))
