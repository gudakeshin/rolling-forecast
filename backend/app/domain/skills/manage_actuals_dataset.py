"""list_actuals_datasets / manage_actuals_dataset -- chat visibility and control
over which ActualsDataset is "current" (see app/services/actuals_resolution.py).

Split into two skill classes (mirroring manage_model_presets.py) since
required_role is enforced once per whole skill: any analyst should be able to
see what's pinned and why, but only admins should hand control back to an
automated feed by unpinning.
"""

from __future__ import annotations

import logging
from typing import Any

from app.domain.base_skill import BaseSkill, SkillContext, SkillResult
from app.models.actuals import ActualsDataset

logger = logging.getLogger(__name__)


def _find(db, dataset_ref: str) -> ActualsDataset | None:
    return db.query(ActualsDataset).filter(ActualsDataset.id == dataset_ref).first()


def _serialize(d: ActualsDataset) -> dict:
    return {
        "id": d.id,
        "source_type": d.source_type,
        "source_name": d.source_name,
        "row_count": d.row_count,
        "period_start": d.period_start,
        "period_end": d.period_end,
        "ingested_at": d.ingested_at.isoformat() if d.ingested_at else None,
        "is_pinned": d.is_pinned,
        "integration_connection_id": d.integration_connection_id,
    }


class ListActualsDatasetsSkill(BaseSkill):
    """Read-only: any analyst can see recent datasets and which one is current."""

    @property
    def name(self) -> str:
        return "list_actuals_datasets"

    @property
    def description(self) -> str:
        return (
            "List recently ingested actuals datasets (manual uploads and "
            "scheduled/API pulls), showing which one is pinned as authoritative "
            "and would be used by generate_baseline/plan_forecast. Use when the "
            "user asks what data is current, why a forecast used a particular "
            "dataset, or wants to check on a scheduled sync."
        )

    @property
    def required_role(self) -> str:
        return "generate"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Max datasets to return, most recent first.",
                    "default": 10,
                },
            },
        }

    async def execute(self, params: dict[str, Any], context: SkillContext) -> SkillResult:
        from app.services.actuals_resolution import resolve_current_dataset

        db = context.db
        limit = int(params.get("limit") or 10)
        rows = (
            db.query(ActualsDataset)
            .order_by(ActualsDataset.ingested_at.desc())
            .limit(limit)
            .all()
        )
        current = resolve_current_dataset(db)
        if not rows:
            return SkillResult.ok(
                message="No actuals datasets ingested yet.",
                data={"datasets": []},
            )
        return SkillResult.ok(
            message=(
                f"Current dataset: {current.source_name} "
                f"({'pinned/manual' if current.is_pinned else 'latest pull'})"
                if current
                else "No current dataset resolved."
            ),
            data={
                "datasets": [_serialize(d) for d in rows],
                "current_dataset_id": current.id if current else None,
            },
            content_blocks=[
                self._table_block(
                    title="Actuals Datasets",
                    columns=[
                        {"key": "source", "label": "Source"},
                        {"key": "ingested_at", "label": "Ingested"},
                        {"key": "pinned", "label": "Pinned"},
                        {"key": "current", "label": "Current"},
                    ],
                    rows=[
                        {
                            "source": f"{d.source_name} ({d.source_type})",
                            "ingested_at": d.ingested_at.isoformat() if d.ingested_at else "—",
                            "pinned": "Yes" if d.is_pinned else "No",
                            "current": "Yes" if current and d.id == current.id else "",
                        }
                        for d in rows
                    ],
                )
            ],
        )


class ManageActualsDatasetSkill(BaseSkill):
    """Mutating: unpin a manually uploaded dataset. Admin only."""

    @property
    def name(self) -> str:
        return "manage_actuals_dataset"

    @property
    def description(self) -> str:
        return (
            "Unpin the currently pinned (manually uploaded) actuals dataset so "
            "the next scheduled/API pull is free to become the dataset "
            "generate_baseline/plan_forecast use. Use when the user says "
            "something like 'stop using my manual upload' or 'let the scheduled "
            "sync take over again'."
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
                    "enum": ["unpin"],
                    "description": "Action to perform.",
                },
                "dataset_id": {
                    "type": "string",
                    "description": (
                        "Dataset to unpin. Omit to unpin whichever pinned "
                        "dataset currently resolves as current."
                    ),
                },
            },
            "required": ["action"],
        }

    async def execute(self, params: dict[str, Any], context: SkillContext) -> SkillResult:
        from app.services.actuals_resolution import resolve_current_dataset

        db = context.db
        action = params.get("action")
        if action != "unpin":
            return SkillResult.fail(f"Unknown action: {action}")

        dataset_id = params.get("dataset_id")
        dataset = _find(db, dataset_id) if dataset_id else resolve_current_dataset(db)
        if not dataset:
            return SkillResult.fail(
                f"Dataset '{dataset_id}' not found." if dataset_id else "No dataset is currently pinned."
            )
        if not dataset.is_pinned:
            return SkillResult.ok(
                message=f"Dataset '{dataset.source_name}' is not pinned — nothing to do.",
                data={"dataset": _serialize(dataset)},
            )

        dataset.is_pinned = False
        db.commit()
        new_current = resolve_current_dataset(db)
        return SkillResult.ok(
            message=(
                f"Unpinned '{dataset.source_name}'. Current dataset is now "
                f"'{new_current.source_name}'."
                if new_current
                else f"Unpinned '{dataset.source_name}'. No dataset is current."
            ),
            data={
                "dataset": _serialize(dataset),
                "current_dataset_id": new_current.id if new_current else None,
            },
        )
