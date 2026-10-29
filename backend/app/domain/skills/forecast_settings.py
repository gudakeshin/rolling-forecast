"""get_forecast_settings / update_forecast_settings — chat access to a company's
forecast configuration (its AnalysisProfile, app/services/analysis_profile.py).

Two skills, not one action-dispatch skill, because required_role applies to a
whole skill: any analyst may see how their company forecasts, only an admin may
change it — the same split as GET/PUT /admin/business-units/{id}/analysis-profile.

Agent-authored writes are two-step. Without ``confirm`` the update skill only
validates and returns a current -> proposed diff; the change is applied only on
a second call with ``confirm=true``, which the skill's .md tells the agent to
send only after the user approves that exact diff. Writes go through the same
validation and audit as the admin UI, tagged ``source="agent"``.
"""

from __future__ import annotations

from typing import Any

from app.domain.base_skill import BaseSkill, SkillContext, SkillResult
from app.models.business_unit import BusinessUnit
from app.services.analysis_profile import (
    ANALYSIS_PROFILE_FIELDS,
    get_analysis_profile_overrides,
    resolve_analysis_settings,
    update_analysis_profile,
    validate_analysis_patch,
)
from app.services.permissions import (
    can_access_business_unit,
    can_view_all_bus,
    resolve_skill_user,
)

_BU_PARAM = {
    "type": "string",
    "description": (
        "Company / business unit name or id. Required for a user who works across "
        "companies; otherwise defaults to the user's own company."
    ),
}


def _resolve_company(context: SkillContext, bu_ref: str | None) -> tuple[BusinessUnit | None, str | None]:
    """(business unit, error message). Same inference rule as generate_baseline:
    a cross-company caller must name the company explicitly."""
    db = context.db
    actor = resolve_skill_user(context)
    if bu_ref:
        bu = (
            db.query(BusinessUnit)
            .filter((BusinessUnit.id == bu_ref) | (BusinessUnit.name == bu_ref))
            .first()
        )
        if bu is None or not can_access_business_unit(actor, bu.id):
            return None, f"Business unit '{bu_ref}' not found."
        return bu, None
    if actor is not None and actor.business_unit_id and not can_view_all_bus(actor):
        return db.query(BusinessUnit).filter(BusinessUnit.id == actor.business_unit_id).first(), None
    return None, (
        "Which company's settings? Pass `business_unit` (name or id) -- your account "
        "isn't tied to exactly one company."
    )


def _fmt(value: Any) -> str:
    if isinstance(value, bool):
        return "on" if value else "off"
    return str(value)


class GetForecastSettingsSkill(BaseSkill):
    """Read-only: a company's effective forecast settings."""

    @property
    def name(self) -> str:
        return "get_forecast_settings"

    @property
    def description(self) -> str:
        return (
            "Show a company's forecast settings (model selection metric, horizon, "
            "materiality, outlier cleaning, benchmark / global models, ...) and which "
            "ones are company overrides versus the global default."
        )

    @property
    def required_role(self) -> str:
        return "generate"

    @property
    def parameters_schema(self) -> dict:
        return {"type": "object", "properties": {"business_unit": _BU_PARAM}}

    async def execute(self, params: dict[str, Any], context: SkillContext) -> SkillResult:
        bu, error = _resolve_company(context, params.get("business_unit"))
        if bu is None:
            return SkillResult.fail(error or "Business unit not found.")
        current = resolve_analysis_settings(context.db, business_unit_id=bu.id).__dict__
        overridden = set(get_analysis_profile_overrides(context.db, business_unit_id=bu.id))
        rows = [
            {
                "setting": name,
                "value": _fmt(current[name]),
                "type": ftype.__name__,
                "source": "company override" if name in overridden else "global default",
            }
            for name, ftype in ANALYSIS_PROFILE_FIELDS.items()
        ]
        return SkillResult.ok(
            message=f"Forecast settings for {bu.name} ({len(overridden)} overridden).",
            data={
                "business_unit_id": bu.id,
                "settings": current,
                "overridden_fields": sorted(overridden),
            },
            content_blocks=[
                self._table_block(
                    title=f"Forecast settings — {bu.name}",
                    columns=[
                        {"key": "setting", "label": "Setting"},
                        {"key": "value", "label": "Value"},
                        {"key": "type", "label": "Type"},
                        {"key": "source", "label": "Source"},
                    ],
                    rows=rows,
                )
            ],
        )


class UpdateForecastSettingsSkill(BaseSkill):
    """Admin: propose, then on confirmation apply, changes to a company's settings."""

    @property
    def name(self) -> str:
        return "update_forecast_settings"

    @property
    def description(self) -> str:
        return (
            "Change a company's forecast settings. First call without confirm to get "
            "a current -> proposed diff and show it to the user; call again with "
            "confirm=true only after the user explicitly approves that diff. Admin only."
        )

    @property
    def required_role(self) -> str:
        return "admin"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "changes": {
                    "type": "object",
                    "description": (
                        "Setting name -> new value, e.g. {\"materiality_share\": 0.05}. "
                        f"Valid settings: {', '.join(ANALYSIS_PROFILE_FIELDS)}."
                    ),
                },
                "confirm": {
                    "type": "boolean",
                    "description": "Apply the changes. Only after the user approved the proposed diff.",
                    "default": False,
                },
                "business_unit": _BU_PARAM,
            },
            "required": ["changes"],
        }

    async def execute(self, params: dict[str, Any], context: SkillContext) -> SkillResult:
        changes = params.get("changes")
        if not isinstance(changes, dict) or not changes:
            return SkillResult.fail("`changes` must be a non-empty object of setting -> value.")
        bu, error = _resolve_company(context, params.get("business_unit"))
        if bu is None:
            return SkillResult.fail(error or "Business unit not found.")
        try:
            coerced = validate_analysis_patch(changes)
        except ValueError as e:
            return SkillResult.fail(str(e))

        db = context.db
        before = resolve_analysis_settings(db, business_unit_id=bu.id).__dict__
        diff = [
            {"setting": name, "current": _fmt(before[name]), "proposed": _fmt(value)}
            for name, value in coerced.items()
            if before[name] != value
        ]
        if not diff:
            return SkillResult.ok(
                message=f"No change: {bu.name} already has those settings.",
                data={"business_unit_id": bu.id, "applied": False, "diff": []},
            )
        table = self._table_block(
            title=f"Forecast settings — {bu.name}",
            columns=[
                {"key": "setting", "label": "Setting"},
                {"key": "current", "label": "Current"},
                {"key": "proposed", "label": "New" if params.get("confirm") else "Proposed"},
            ],
            rows=diff,
        )

        if not params.get("confirm"):
            return SkillResult.ok(
                message=(
                    f"Proposed {len(diff)} change(s) to {bu.name}'s forecast settings. Nothing "
                    "has been changed yet: show this diff to the user and apply it with "
                    "confirm=true only if they approve it."
                ),
                data={"business_unit_id": bu.id, "applied": False, "diff": diff},
                content_blocks=[table],
            )

        changed = {row["setting"]: coerced[row["setting"]] for row in diff}
        update_analysis_profile(
            db,
            business_unit_id=bu.id,
            patch=changed,
            actor=resolve_skill_user(context),
            source="agent",
        )
        db.commit()
        return SkillResult.ok(
            message=(
                f"Updated {len(diff)} forecast setting(s) for {bu.name}. They apply to the "
                "next forecast run."
            ),
            data={"business_unit_id": bu.id, "applied": True, "diff": diff},
            content_blocks=[table],
        )
