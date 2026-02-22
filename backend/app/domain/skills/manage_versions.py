"""ManageVersions skill -- list, load, diff, and manage forecast versions."""

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.domain.base_skill import BaseSkill, SkillContext, SkillResult
from app.models.forecast import ForecastVersion

logger = logging.getLogger(__name__)


class ManageVersionsSkill(BaseSkill):
    """Skill to list, load, and manage forecast versions."""

    @property
    def name(self) -> str:
        return "manage_versions"

    @property
    def description(self) -> str:
        return (
            "List, load, and manage forecast versions. Can list all versions, "
            "show details of a specific version, set the active version, "
            "or update a version's status (draft, in_review, approved, published). "
            "Use this when the user asks about forecast versions, wants to see history, "
            "or wants to change the active forecast."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "Action to perform",
                    "enum": ["list", "get", "set_active", "update_status"],
                },
                "version_id": {
                    "type": "string",
                    "description": "Version ID for get/set_active/update_status actions",
                },
                "status": {
                    "type": "string",
                    "description": "New status for update_status action",
                    "enum": ["draft", "in_review", "approved", "published", "archived"],
                },
            },
            "required": ["action"],
        }

    async def execute(self, params: dict[str, Any], context: SkillContext) -> SkillResult:
        db: Session = context.db
        action = params.get("action", "list")

        if action == "list":
            return await self._list_versions(db, context)
        elif action == "get":
            return await self._get_version(db, params.get("version_id"), context)
        elif action == "set_active":
            return await self._set_active(db, params.get("version_id"), context)
        elif action == "update_status":
            return await self._update_status(
                db, params.get("version_id"), params.get("status"), context
            )
        else:
            return SkillResult.fail(f"Unknown action: {action}")

    async def _list_versions(self, db: Session, context: SkillContext) -> SkillResult:
        versions = (
            db.query(ForecastVersion)
            .order_by(ForecastVersion.created_at.desc())
            .limit(20)
            .all()
        )

        if not versions:
            return SkillResult.ok(
                message="No forecast versions found. Generate a forecast first.",
                data={"versions": []},
            )

        active_id = context.context_manager.get_active_version_id()

        rows = []
        for v in versions:
            rows.append({
                "name": v.name + (" *" if v.id == active_id else ""),
                "status": v.status,
                "lines": str(v.total_line_items),
                "high": str(v.high_confidence_count),
                "low": str(v.low_confidence_count),
                "created": v.created_at.strftime("%Y-%m-%d %H:%M") if v.created_at else "",
            })

        content_blocks = [
            self._table_block(
                title=f"Forecast Versions ({len(versions)} total)",
                columns=[
                    {"key": "name", "label": "Version"},
                    {"key": "status", "label": "Status"},
                    {"key": "lines", "label": "Lines"},
                    {"key": "high", "label": "High Conf"},
                    {"key": "low", "label": "Low Conf"},
                    {"key": "created", "label": "Created"},
                ],
                rows=rows,
            ),
        ]

        return SkillResult.ok(
            message=f"Found {len(versions)} forecast versions. (* = active)",
            data={
                "versions": [
                    {"id": v.id, "name": v.name, "status": v.status}
                    for v in versions
                ],
                "active_id": active_id,
            },
            content_blocks=content_blocks,
        )

    async def _get_version(
        self, db: Session, version_id: str | None, context: SkillContext
    ) -> SkillResult:
        version_id = version_id or context.context_manager.get_active_version_id()
        if not version_id:
            return SkillResult.fail("No version specified or active.")

        version = db.query(ForecastVersion).filter(ForecastVersion.id == version_id).first()
        if not version:
            return SkillResult.fail(f"Version '{version_id}' not found.")

        content_blocks = [
            self._table_block(
                title=f"Forecast Version: {version.name}",
                columns=[
                    {"key": "field", "label": "Field"},
                    {"key": "value", "label": "Value"},
                ],
                rows=[
                    {"field": "Name", "value": version.name},
                    {"field": "Status", "value": version.status},
                    {"field": "Type", "value": version.version_type},
                    {"field": "Horizon", "value": f"{version.horizon_months} months"},
                    {"field": "Base Period", "value": version.base_period or "N/A"},
                    {"field": "Line Items", "value": str(version.total_line_items)},
                    {"field": "High Confidence", "value": str(version.high_confidence_count)},
                    {"field": "Medium Confidence", "value": str(version.medium_confidence_count)},
                    {"field": "Low Confidence", "value": str(version.low_confidence_count)},
                    {"field": "Overrides", "value": str(version.override_count)},
                    {"field": "Generation Time", "value": f"{version.generation_time_seconds:.1f}s" if version.generation_time_seconds else "N/A"},
                    {"field": "Created", "value": version.created_at.strftime("%Y-%m-%d %H:%M") if version.created_at else "N/A"},
                ],
            ),
            self._panel_trigger(
                panel="forecast_table",
                params={"version_id": version.id},
                label="View Full Forecast Table",
            ),
        ]

        return SkillResult.ok(
            message=f"Version {version.name}: {version.total_line_items} lines, status={version.status}",
            data={"version_id": version.id, "name": version.name, "status": version.status},
            content_blocks=content_blocks,
        )

    async def _set_active(
        self, db: Session, version_id: str | None, context: SkillContext
    ) -> SkillResult:
        if not version_id:
            return SkillResult.fail("No version ID specified.")

        version = db.query(ForecastVersion).filter(ForecastVersion.id == version_id).first()
        if not version:
            return SkillResult.fail(f"Version '{version_id}' not found.")

        context.context_manager.set_active_version_id(version_id)

        return SkillResult.ok(
            message=f"Set active forecast version to {version.name}.",
            data={"version_id": version_id, "name": version.name},
            content_blocks=[
                self._text_block(f"Active forecast version is now **{version.name}**.")
            ],
        )

    async def _update_status(
        self, db: Session, version_id: str | None, status: str | None, context: SkillContext
    ) -> SkillResult:
        if not version_id:
            return SkillResult.fail("No version ID specified.")
        if not status:
            return SkillResult.fail("No status specified.")

        version = db.query(ForecastVersion).filter(ForecastVersion.id == version_id).first()
        if not version:
            return SkillResult.fail(f"Version '{version_id}' not found.")

        old_status = version.status
        version.status = status
        db.commit()

        return SkillResult.ok(
            message=f"Updated {version.name} status: {old_status} -> {status}",
            data={"version_id": version_id, "old_status": old_status, "new_status": status},
            content_blocks=[
                self._text_block(f"**{version.name}** status updated: {old_status} -> **{status}**")
            ],
        )
