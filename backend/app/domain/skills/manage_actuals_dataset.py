"""list_actuals_datasets / manage_actuals_dataset -- chat visibility and control
over which ActualsDataset is "current" (see app/services/actuals_resolution.py),
plus hard-deleting a dataset a company no longer needs.

Split into two skill classes (mirroring manage_model_presets.py) since
required_role is enforced once per whole skill: any analyst should be able to
see what's pinned and why, but only admins should unpin or delete.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from app.domain.base_skill import BaseSkill, SkillContext, SkillResult
from app.models.actuals import ActualsDataset
from app.models.forecast import ForecastVersion

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
        "business_unit_id": d.business_unit_id,
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
            "scheduled/API pulls) for the caller's company, showing which one "
            "is pinned as authoritative and would be used by "
            "generate_baseline/plan_forecast. Use when the user asks what "
            "data is current, why a forecast used a particular dataset, or "
            "wants to check on a scheduled sync."
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
        from app.services.permissions import can_view_all_bus, resolve_skill_user

        db = context.db
        actor = resolve_skill_user(context)
        limit = int(params.get("limit") or 10)
        q = db.query(ActualsDataset)
        bu_id = actor.business_unit_id if actor else None
        if actor is not None and not can_view_all_bus(actor):
            q = q.filter(ActualsDataset.business_unit_id == bu_id)
        rows = q.order_by(ActualsDataset.ingested_at.desc()).limit(limit).all()
        current = resolve_current_dataset(
            db, business_unit_id=bu_id if actor and not can_view_all_bus(actor) else None
        )
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
    """Mutating: unpin or hard-delete an actuals dataset. Admin only."""

    @property
    def name(self) -> str:
        return "manage_actuals_dataset"

    @property
    def description(self) -> str:
        return (
            "Unpin the currently pinned (manually uploaded) actuals dataset so "
            "the next scheduled/API pull is free to become current, or "
            "permanently delete a dataset (its records and uploaded file) that "
            "a company no longer needs. Use when the user says something like "
            "'stop using my manual upload', 'let the scheduled sync take over "
            "again', or 'delete that upload'."
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
                    "enum": ["unpin", "delete"],
                    "description": "Action to perform.",
                },
                "dataset_id": {
                    "type": "string",
                    "description": (
                        "Dataset to act on. For unpin, omit to target whichever "
                        "pinned dataset currently resolves as current. Required for delete."
                    ),
                },
            },
            "required": ["action"],
        }

    async def execute(self, params: dict[str, Any], context: SkillContext) -> SkillResult:
        from app.services.actuals_resolution import resolve_current_dataset
        from app.services.permissions import (
            can_access_business_unit,
            can_view_all_bus,
            resolve_skill_user,
        )

        db = context.db
        action = params.get("action")
        actor = resolve_skill_user(context)
        # Scope the default ("whichever dataset is current") resolution to the
        # caller's own company unless they can see across all of them --
        # otherwise a non-admin's un-targeted unpin could resolve to another
        # company's current dataset and be wrongly refused as "not found".
        actor_bu_id = None
        if actor is not None and not can_view_all_bus(actor):
            actor_bu_id = actor.business_unit_id

        if action == "unpin":
            dataset_id = params.get("dataset_id")
            dataset = (
                _find(db, dataset_id)
                if dataset_id
                else resolve_current_dataset(db, business_unit_id=actor_bu_id)
            )
            if not dataset:
                return SkillResult.fail(
                    f"Dataset '{dataset_id}' not found." if dataset_id else "No dataset is currently pinned."
                )
            if not can_access_business_unit(actor, dataset.business_unit_id):
                return SkillResult.fail(f"Dataset '{dataset_id}' not found.")
            if not dataset.is_pinned:
                return SkillResult.ok(
                    message=f"Dataset '{dataset.source_name}' is not pinned — nothing to do.",
                    data={"dataset": _serialize(dataset)},
                )

            dataset.is_pinned = False
            db.commit()
            new_current = resolve_current_dataset(db, business_unit_id=dataset.business_unit_id)
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

        if action == "delete":
            dataset_id = params.get("dataset_id")
            if not dataset_id:
                return SkillResult.fail("dataset_id is required for delete.")
            dataset = _find(db, dataset_id)
            if not dataset:
                return SkillResult.fail(f"Dataset '{dataset_id}' not found.")
            if not can_access_business_unit(actor, dataset.business_unit_id):
                return SkillResult.fail(f"Dataset '{dataset_id}' not found.")

            blockers = (
                db.query(ForecastVersion)
                .filter(ForecastVersion.actuals_dataset_id == dataset.id)
                .all()
            )
            if blockers:
                names = ", ".join(v.name for v in blockers[:5])
                more = f" and {len(blockers) - 5} more" if len(blockers) > 5 else ""
                return SkillResult.fail(
                    f"Can't delete '{dataset.source_name}' — {len(blockers)} forecast "
                    f"version(s) were built from it ({names}{more}). Delete those "
                    "versions first."
                )

            source_name = dataset.source_name
            storage_path = dataset.storage_path
            if storage_path:
                try:
                    if os.path.exists(storage_path):
                        os.unlink(storage_path)
                except OSError:
                    logger.warning("Failed to remove uploaded file %s", storage_path, exc_info=True)

            from app.services.audit import record_audit

            record_audit(
                db,
                action="actuals_dataset.delete",
                entity_type="actuals_dataset",
                entity_id=dataset.id,
                actor_id=getattr(actor, "id", None),
                actor_username=getattr(actor, "username", "unknown"),
                details={"source_name": source_name, "row_count": dataset.row_count},
                commit=False,
            )
            db.delete(dataset)  # cascades to ActualsRecord
            db.commit()
            return SkillResult.ok(
                message=f"Deleted dataset '{source_name}' and its actuals records.",
                data={"deleted_dataset_id": dataset_id},
            )

        return SkillResult.fail(f"Unknown action: {action}")
