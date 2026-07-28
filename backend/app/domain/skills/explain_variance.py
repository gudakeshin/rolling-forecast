"""explain_variance skill — Phase 6a/6b attribution via chat (U2)."""

from __future__ import annotations

import logging
from typing import Any

from app.domain.base_skill import BaseSkill, SkillContext, SkillResult
from app.models.forecast import ForecastVersion
from app.models.line_item import LineItem
from app.services.permissions import resolve_skill_user, user_can_view_line_item

logger = logging.getLogger(__name__)


class ExplainVarianceSkill(BaseSkill):
    @property
    def name(self) -> str:
        return "explain_variance"

    @property
    def description(self) -> str:
        return (
            "Explain why a forecast line moved using honest variance attribution "
            "(identity Q×P/mix when drivers exist, else FX, else unattributed override text). "
            "Use for questions like 'why did revenue move?', 'decompose the miss', "
            "or 'show the variance bridge'."
        )

    @property
    def required_role(self) -> str | None:
        return None

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "version_id": {
                    "type": "string",
                    "description": "Forecast version (defaults to active)",
                },
                "line_item_name": {
                    "type": "string",
                    "description": "Line item name or account code",
                },
                "line_item_id": {"type": "integer"},
                "period_from": {"type": "string"},
                "period_to": {"type": "string"},
                "basis": {
                    "type": "string",
                    "enum": ["auto", "identity", "fx", "override"],
                    "default": "auto",
                },
                "convention": {
                    "type": "string",
                    "enum": ["volume_first", "price_first"],
                    "default": "volume_first",
                },
                "open_panel": {"type": "boolean", "default": True},
            },
            "required": [],
        }

    async def execute(self, params: dict[str, Any], context: SkillContext) -> SkillResult:
        from app.services.variance_attribution import attribute_variance

        db = context.db
        version_id = (
            params.get("version_id")
            or context.context_manager.get_active_version_id()
        )
        if not version_id:
            return SkillResult.fail("No forecast version active. Select or generate one first.")

        version = db.query(ForecastVersion).filter(ForecastVersion.id == version_id).first()
        if not version:
            return SkillResult.fail(f"Version '{version_id}' not found.")

        line_item_id = params.get("line_item_id")
        if not line_item_id and params.get("line_item_name"):
            name = str(params["line_item_name"]).strip()
            li = (
                db.query(LineItem)
                .filter(
                    (LineItem.name.ilike(f"%{name}%"))
                    | (LineItem.account_code.ilike(f"%{name}%"))
                )
                .first()
            )
            if not li:
                return SkillResult.fail(f"Line item matching '{name}' not found.")
            line_item_id = li.id

        if not line_item_id:
            # Open panel for interactive exploration when no line specified
            return SkillResult.ok(
                message="Opening explainability panel for the active version.",
                panel_payload={
                    "panel": "explainability",
                    "params": {"version_id": version_id},
                    "title": "Explain Variance",
                },
            )

        li = db.query(LineItem).filter(LineItem.id == int(line_item_id)).first()
        user = resolve_skill_user(context)
        if li is None or (user and not user_can_view_line_item(user, li)):
            return SkillResult.fail("Line item not found or not visible.")

        attr = attribute_variance(
            db,
            line_item_id=int(line_item_id),
            period_from=params.get("period_from"),
            period_to=params.get("period_to"),
            basis=params.get("basis") or "auto",
            convention=params.get("convention") or "volume_first",
            version_id=version_id,
        )

        buckets = attr.get("buckets") or {}
        bucket_lines = [
            f"- **{k.replace('_', ' ').title()}**: {float(v):,.2f}"
            for k, v in buckets.items()
        ]
        text = (
            f"### Variance for {li.name}\n"
            f"Method: `{attr.get('method')}`"
            + (f" · convention `{attr.get('convention')}`" if attr.get("convention") else "")
            + f"\nExplained: **{attr.get('explained_pct', 0)}%**\n"
        )
        if bucket_lines:
            text += "\n" + "\n".join(bucket_lines)

        blocks: list[dict] = [self._text_block(text)]
        if attr.get("waterfall"):
            blocks.append({"type": "chart", "data": attr["waterfall"]})

        panel = None
        if params.get("open_panel", True):
            panel = {
                "panel": "explainability",
                "params": {
                    "version_id": version_id,
                    "focus_line_item_id": li.id,
                },
                "title": "Explain Variance",
            }

        return SkillResult.ok(
            message=f"Attributed {li.name} via {attr.get('method')} "
            f"({attr.get('explained_pct', 0)}% explained).",
            data={"line_item_id": li.id, "attribution": attr},
            content_blocks=blocks,
            panel_payload=panel,
        )
