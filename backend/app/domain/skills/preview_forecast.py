"""preview_forecast skill: instant Chronos-2 preview of an uploaded dataset (Phase E4)."""

from __future__ import annotations

from typing import Any

from app.domain.base_skill import BaseSkill, SkillContext, SkillResult
from app.models.actuals import ActualsDataset
from app.services.permissions import can_access_business_unit, can_view_all_bus, resolve_skill_user


def _fmt(value: float) -> str:
    if abs(value) >= 1_000_000:
        return f"${value / 1_000_000:.1f}M"
    if abs(value) >= 1_000:
        return f"${value / 1_000:.0f}K"
    return f"${value:.0f}"


class PreviewForecastSkill(BaseSkill):
    @property
    def name(self) -> str:
        return "preview_forecast"

    @property
    def description(self) -> str:
        return (
            "Instant forecast preview of an uploaded actuals dataset from the Chronos-2 "
            "foundation model, in seconds and without saving anything. Use right after an "
            "upload, or when the user wants a quick first look before generating the real "
            "baseline. Not a substitute for generate_baseline."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "dataset_id": {
                    "type": "string",
                    "description": "Actuals dataset to preview (defaults to the most recent one)",
                },
                "horizon_months": {"type": "integer", "default": 12, "minimum": 1, "maximum": 24},
            },
        }

    async def execute(self, params: dict[str, Any], context: SkillContext) -> SkillResult:
        from app.services.foundation_preview import build_preview

        db = context.db
        user = resolve_skill_user(context)
        dataset_id = params.get("dataset_id") or context.context_manager.get_memory("last_dataset_id")
        q = db.query(ActualsDataset)
        if dataset_id:
            dataset = q.filter(ActualsDataset.id == dataset_id).first()
        else:
            if user is not None and not can_view_all_bus(user):
                q = q.filter(ActualsDataset.business_unit_id == user.business_unit_id)
            dataset = q.order_by(ActualsDataset.ingested_at.desc()).first()
        if dataset is None or (
            user is not None and not can_access_business_unit(user, dataset.business_unit_id)
        ):
            return SkillResult.fail("No actuals dataset found to preview. Upload actuals first.")

        data = build_preview(db, dataset, horizon=int(params.get("horizon_months") or 12))
        if not data.get("available"):
            return SkillResult.fail(
                f"No instant preview: {data.get('reason')}. Generate the baseline as usual."
            )
        lines = data["lines"]
        top = "\n".join(
            f"- {ln['line_item_name']}: {_fmt(ln['total_p50'])} over the next {data['horizon']} periods"
            for ln in lines[:5]
        )
        msg = (
            f"Instant Chronos-2 preview of **{len(lines)}** line(s), next {data['horizon']} periods "
            f"(not saved; generate the baseline for the forecast you review):\n{top}"
        )
        if data.get("skipped"):
            msg += f"\n\nNot previewed: {len(data['skipped'])} line(s) (too short or all zero)."
        return SkillResult.ok(
            message=msg,
            data={"dataset_id": dataset.id, "horizon": data["horizon"], "lines": len(lines),
                  "skipped": len(data.get("skipped") or [])},
            content_blocks=[self._text_block(msg)],
            panel_payload={
                "panel": "forecast_preview",
                "params": {"dataset_id": dataset.id},
                "title": "Instant forecast preview",
            },
        )
