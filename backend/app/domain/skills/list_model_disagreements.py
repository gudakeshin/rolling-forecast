"""list_model_disagreements skill: lines where Chronos-2 disagrees (Phase E2)."""

from __future__ import annotations

from typing import Any

from app.domain.base_skill import BaseSkill, SkillContext, SkillResult
from app.models.forecast import ForecastLineResult
from app.models.line_item import LineItem
from app.services.permissions import line_item_scope_filter, resolve_skill_user
from app.services.second_opinion import disagreeing_opinions, review_stats


class ListModelDisagreementsSkill(BaseSkill):
    @property
    def name(self) -> str:
        return "list_model_disagreements"

    @property
    def description(self) -> str:
        return (
            "List the lines in a forecast version where the published forecast sits outside "
            "the Chronos-2 foundation model's P10-P90 for most of the horizon (a 'Models "
            "disagree' second opinion), and how review has treated them. Use when the user "
            "asks where to look first, which lines are risky, or about the second opinion."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "version_id": {
                    "type": "string",
                    "description": "Forecast version (defaults to the active one)",
                },
                "limit": {"type": "integer", "default": 10, "minimum": 1, "maximum": 50},
            },
        }

    async def execute(self, params: dict[str, Any], context: SkillContext) -> SkillResult:
        db = context.db
        version_id = params.get("version_id") or context.context_manager.get_active_version_id()
        if not version_id:
            return SkillResult.fail("No active forecast version. Generate or select one first.")
        limit = int(params.get("limit") or 10)
        user = resolve_skill_user(context)

        q = (
            db.query(ForecastLineResult, LineItem)
            .join(LineItem, LineItem.id == ForecastLineResult.line_item_id)
            .filter(ForecastLineResult.version_id == version_id)
        )
        if user is not None:
            q = line_item_scope_filter(q, user, LineItem)
        rows = q.all()
        names = {li.id: li for _, li in rows}
        line_ids = sorted(names)
        opinions = disagreeing_opinions(db, version_id, line_ids)
        flagged = {li_id: names[li_id] for li_id in opinions}

        items = []
        for li_id, li in flagged.items():
            op = opinions.get(li_id, {})
            items.append({
                "line_item_id": li_id,
                "line_item_name": li.name,
                "category": li.category,
                "outside_share": op.get("outside_share"),
                "direction": op.get("direction"),
            })
        items.sort(key=lambda d: -(d["outside_share"] or 0))
        stats = review_stats(db, version_id, line_ids)

        if not items:
            msg = "Chronos-2 agrees with the published forecast on every line in this version."
        else:
            listed = "\n".join(
                f"- {it['line_item_name']}: outside Chronos-2's range in "
                f"{round((it['outside_share'] or 0) * 100)}% of periods"
                + (f" ({it['direction']} it)" if it["direction"] in ("above", "below") else "")
                for it in items[:limit]
            )
            reviewed = stats["flagged_reviewed"]
            msg = (
                f"Models disagree on **{len(items)}** line(s); {reviewed} of them reviewed so far.\n"
                f"{listed}"
            )
            if stats["share_of_reviews"] is not None:
                msg += (
                    f"\n\n{round(stats['share_of_reviews'] * 100)}% of the lines reviewed in this "
                    "version were flagged ones."
                )
        return SkillResult.ok(
            message=msg,
            data={"version_id": version_id, "items": items[:limit], "review_stats": stats},
            content_blocks=[self._text_block(msg)],
            panel_payload=(
                {"panel": "review_queue", "params": {"version_id": version_id}, "title": "Review Queue"}
                if items
                else None
            ),
        )
