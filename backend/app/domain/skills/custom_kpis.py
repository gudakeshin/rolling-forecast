"""get_custom_kpis — chat access to a company's custom KPIs for a forecast version
(definitions: app/services/kpis.py; managed via /api/kpis or the Executive
Dashboard)."""

from __future__ import annotations

from typing import Any

from app.domain.base_skill import BaseSkill, SkillContext, SkillResult
from app.models.forecast import ForecastVersion
from app.services.kpis import evaluate_kpis
from app.services.permissions import can_access_business_unit, resolve_skill_user


def format_kpi_value(value: float | None, display_format: str) -> str:
    if value is None:
        return "—"
    if display_format == "percent":
        return f"{value * 100:,.1f}%"
    if display_format == "currency":
        return f"{value:,.0f}"
    if display_format == "ratio":
        return f"{value:,.2f}x"
    return f"{value:,.2f}"


class GetCustomKpisSkill(BaseSkill):
    @property
    def name(self) -> str:
        return "get_custom_kpis"

    @property
    def description(self) -> str:
        return (
            "Evaluate the company's custom KPIs (admin-defined formulas over line "
            "items and categories, e.g. gross margin %) for a forecast version."
        )

    @property
    def required_role(self) -> str:
        return "input"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "version_id": {
                    "type": "string",
                    "description": "Forecast version (uses the active version if omitted)",
                },
            },
        }

    async def execute(self, params: dict[str, Any], context: SkillContext) -> SkillResult:
        version_id = params.get("version_id") or context.context_manager.get_active_version_id()
        if not version_id:
            return SkillResult.fail("No active forecast version.")
        version = context.db.query(ForecastVersion).filter(ForecastVersion.id == version_id).first()
        if version is None or not can_access_business_unit(
            resolve_skill_user(context), version.business_unit_id
        ):
            return SkillResult.fail(f"Version '{version_id}' not found.")

        kpis = evaluate_kpis(context.db, version)
        if not kpis:
            return SkillResult.ok(
                message=(
                    "No custom KPIs are defined for this company. An admin can add them "
                    "from the Executive Dashboard."
                ),
                data={"version_id": version.id, "kpis": []},
            )
        rows = [
            {
                "kpi": k["name"],
                "value": format_kpi_value(k["total"], k["display_format"]),
                "formula": k["formula"],
                "note": k["error"]
                or (f"no forecast values for {', '.join(k['missing'])}" if k["missing"] else ""),
            }
            for k in kpis
        ]
        return SkillResult.ok(
            message=f"{len(kpis)} custom KPI(s) for {version.name} (horizon totals).",
            data={"version_id": version.id, "kpis": kpis},
            content_blocks=[
                self._table_block(
                    title=f"Custom KPIs — {version.name}",
                    columns=[
                        {"key": "kpi", "label": "KPI"},
                        {"key": "value", "label": "Horizon total"},
                        {"key": "formula", "label": "Formula"},
                        {"key": "note", "label": "Note"},
                    ],
                    rows=rows,
                )
            ],
        )
