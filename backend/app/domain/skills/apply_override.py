"""ApplyOverride skill -- manually override forecast values with DAG recalculation.

Handles PRD Section 10 edge cases:
- EC5: Circular dependency detection (via DependencyGraphManager)
- EC6: Override creates impossible value (soft validation warnings)
- EC10: Concurrent users overriding the same line item (detect & notify)
"""

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.domain.base_skill import BaseSkill, SkillContext, SkillResult
from app.models.forecast import ForecastVersion, ForecastLineResult
from app.models.line_item import LineItem
from app.models.override import Override
from app.services.error_handlers import OverrideValidator, check_concurrent_override

logger = logging.getLogger(__name__)


class ApplyOverrideSkill(BaseSkill):
    """Skill to apply manual overrides to forecast values with DAG recalculation."""

    @property
    def name(self) -> str:
        return "apply_override"

    @property
    def description(self) -> str:
        return (
            "Apply a manual override to a forecast line item value. "
            "Requires a reason (minimum 10 characters). Automatically recalculates "
            "all downstream dependent line items using the P&L dependency graph. "
            "Can also revert a previous override. "
            "Use this when the user wants to change a specific forecast value, "
            "override the model's output, or revert a previous override."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "Action to perform: apply (new override), revert (undo override), or list (show overrides)",
                    "enum": ["apply", "revert", "list"],
                    "default": "apply",
                },
                "line_item_name": {
                    "type": "string",
                    "description": "Name or account code of the line item to override",
                },
                "period": {
                    "type": "string",
                    "description": "Period to override (e.g., '2026-03'). Use 'all' for all forecast periods.",
                },
                "new_value": {
                    "type": "number",
                    "description": "The override value to set",
                },
                "reason": {
                    "type": "string",
                    "description": "Reason for the override (minimum 10 characters, required for audit trail)",
                },
                "carry_forward": {
                    "type": "boolean",
                    "description": "Whether this override should persist to the next forecast cycle",
                    "default": True,
                },
                "version_id": {
                    "type": "string",
                    "description": "Forecast version ID (uses active version if not specified)",
                },
                "override_id": {
                    "type": "string",
                    "description": "Override ID (for revert action)",
                },
            },
            "required": [],
        }

    @property
    def required_role(self) -> str:
        return "override"

    async def execute(self, params: dict[str, Any], context: SkillContext) -> SkillResult:
        action = params.get("action", "apply")
        db: Session = context.db

        if action == "list":
            return await self._list_overrides(db, params, context)
        elif action == "revert":
            return await self._revert_override(db, params, context)
        else:
            return await self._apply_override(db, params, context)

    async def _apply_override(
        self, db: Session, params: dict[str, Any], context: SkillContext
    ) -> SkillResult:
        """Apply a new override to a forecast line item."""
        version_id = params.get("version_id") or context.context_manager.get_active_version_id()
        if not version_id:
            return SkillResult.fail("No active forecast version. Generate a forecast first.")

        version = db.query(ForecastVersion).filter(ForecastVersion.id == version_id).first()
        if not version:
            return SkillResult.fail(f"Forecast version '{version_id}' not found.")

        if version.status not in ("draft", "in_review"):
            # SOX immutability: clone a new editable draft instead of mutating published snapshots
            from app.services.versioning import ensure_editable_version

            try:
                version, cloned = ensure_editable_version(
                    db, version, user_id=context.user_id, clone_if_locked=True
                )
                version_id = version.id
                if cloned:
                    context.context_manager.set_memory("active_version_id", version_id)
            except ValueError as e:
                return SkillResult.fail(str(e))

        # Acquire edit locks for concurrent safety
        from app.services.locks import acquire_lock, release_lock, LockConflictError

        # Find the line item (BU-scoped)
        from app.services.permissions import resolve_skill_user, scoped_line_items, user_can_view_line_item

        line_item_name = params.get("line_item_name", "")
        if not line_item_name:
            return SkillResult.fail("Please specify which line item to override.")

        actor = resolve_skill_user(context)
        line_item = (
            scoped_line_items(db, actor)
            .filter(
                (LineItem.name.ilike(f"%{line_item_name}%"))
                | (LineItem.account_code.ilike(f"%{line_item_name}%"))
            )
            .first()
        )
        if not line_item:
            return SkillResult.fail(
                f"Line item '{line_item_name}' not found. Use the query skill to list available line items."
            )
        if not user_can_view_line_item(actor, line_item):
            return SkillResult.fail(
                f"You do not have access to line item '{line_item_name}'."
            )

        period = params.get("period")
        new_value = params.get("new_value")
        reason = params.get("reason", "")
        carry_forward = params.get("carry_forward", True)

        if new_value is None:
            return SkillResult.fail("Please specify the new value for the override.")
        if not reason or len(reason) < 10:
            return SkillResult.fail(
                "Override reason is required (minimum 10 characters) for the audit trail."
            )

        # Handle single or multiple period overrides
        if period == "all":
            results = (
                db.query(ForecastLineResult)
                .filter(
                    ForecastLineResult.version_id == version_id,
                    ForecastLineResult.line_item_id == line_item.id,
                )
                .all()
            )
        else:
            result = (
                db.query(ForecastLineResult)
                .filter(
                    ForecastLineResult.version_id == version_id,
                    ForecastLineResult.line_item_id == line_item.id,
                    ForecastLineResult.period == period,
                )
                .first()
            )
            results = [result] if result else []

        if not results:
            return SkillResult.fail(
                f"No forecast data found for '{line_item.name}' in the specified period(s)."
            )

        # EC6: Validate override value (soft warnings)
        override_warnings = []
        for r in results:
            warnings = OverrideValidator.validate(
                line_item_name=line_item.name,
                category=line_item.category,
                allow_negative=line_item.allow_negative,
                original_value=r.p50,
                override_value=new_value,
            )
            for w in warnings:
                if w["message"] not in [x["message"] for x in override_warnings]:
                    override_warnings.append(w)

        # EC10: Check for concurrent overrides
        concurrent_notices = []
        for r in results:
            concurrent = check_concurrent_override(
                db, version_id, line_item.id, r.period, context.user_id
            )
            if concurrent:
                concurrent_notices.append(concurrent)

        # Apply locks
        locked_periods = []
        try:
            for line_result in results:
                acquire_lock(
                    version_id,
                    line_item.id,
                    line_result.period,
                    context.user_id,
                    username=getattr(getattr(context, "user", None), "username", None),
                )
                locked_periods.append(line_result.period)
        except LockConflictError as e:
            for p in locked_periods:
                release_lock(version_id, line_item.id, p, context.user_id)
            return SkillResult.fail(str(e))

        # Apply overrides on the (possibly cloned) draft version
        from app.services.overrides import recalculate_and_reconcile

        overrides_created = []
        total_recalc = 0
        touched_periods: list[str] = []

        try:
            for line_result in results:
                original_value = (
                    line_result.override_value
                    if line_result.is_overridden
                    else line_result.p50
                )

                override = Override(
                    version_id=version_id,
                    line_item_id=line_item.id,
                    period=line_result.period,
                    original_model_value=original_value,
                    override_value=new_value,
                    reason=reason,
                    carry_forward=carry_forward,
                    user_id=context.user_id,
                    status="active",
                )
                db.add(override)

                line_result.is_overridden = True
                line_result.override_value = new_value
                touched_periods.append(line_result.period)

                overrides_created.append({
                    "period": line_result.period,
                    "original": original_value,
                    "new": new_value,
                    "override": override,
                })

            # One batched recalc + MinT pass for all touched periods
            total_recalc = recalculate_and_reconcile(
                db, version_id, [line_item.id], touched_periods or None
            )
            for row in overrides_created:
                row["override"].downstream_recalc_count = total_recalc
                row["recalculated"] = total_recalc
                del row["override"]

            version.override_count = (
                db.query(Override)
                .filter(Override.version_id == version_id, Override.status == "active")
                .count()
            )

            from app.services.audit import record_audit

            record_audit(
                db,
                action="forecast.override",
                entity_type="forecast_version",
                entity_id=version_id,
                actor_id=context.user_id,
                details={
                    "line_item": line_item.name,
                    "periods": [o["period"] for o in overrides_created],
                    "new_value": new_value,
                    "reason": reason,
                },
            )
            db.commit()
        finally:
            for p in locked_periods:
                release_lock(version_id, line_item.id, p, context.user_id)

        # Build response
        rows = []
        for o in overrides_created:
            change_pct = ((o["new"] - o["original"]) / abs(o["original"]) * 100) if o["original"] != 0 else 0
            rows.append({
                "period": o["period"],
                "original": f"${o['original']:,.0f}",
                "new_value": f"${o['new']:,.0f}",
                "change": f"{'+' if change_pct > 0 else ''}{change_pct:.1f}%",
                "recalculated": str(o["recalculated"]),
            })

        content_blocks = [
            self._text_block(
                f"Applied override to **{line_item.name}** ({len(overrides_created)} period(s))"
            ),
            self._table_block(
                title="Override Details",
                columns=[
                    {"key": "period", "label": "Period"},
                    {"key": "original", "label": "Original"},
                    {"key": "new_value", "label": "New Value"},
                    {"key": "change", "label": "Change"},
                    {"key": "recalculated", "label": "Downstream Recalc"},
                ],
                rows=rows,
            ),
        ]

        if total_recalc > 0:
            content_blocks.append(
                self._text_block(
                    f"Recalculated **{total_recalc}** downstream dependent line items via the P&L dependency graph."
                )
            )

        content_blocks.append(
            self._text_block(f"Reason: _{reason}_")
        )

        for w in override_warnings:
            content_blocks.append(
                self._text_block(f"**{w['level'].title()}:** {w['message']}")
            )

        for notice in concurrent_notices:
            content_blocks.append(
                self._text_block(f"**Concurrent Edit:** {notice['message']}")
            )

        return SkillResult.ok(
            message=f"Override applied to {line_item.name}: {len(overrides_created)} period(s), {total_recalc} downstream recalculations",
            data={
                "line_item_id": line_item.id,
                "line_item_name": line_item.name,
                "overrides_count": len(overrides_created),
                "downstream_recalc": total_recalc,
                "version_id": version_id,
            },
            content_blocks=content_blocks,
        )

    async def _revert_override(
        self, db: Session, params: dict[str, Any], context: SkillContext
    ) -> SkillResult:
        """Revert an existing override."""
        from app.services.overrides import revert_override
        from app.services.permissions import resolve_skill_user, scoped_line_items

        override_id = params.get("override_id")
        version_id = params.get("version_id") or context.context_manager.get_active_version_id()

        if not override_id and not params.get("line_item_name"):
            return SkillResult.fail("Please specify which override to revert (by override ID or line item name).")

        overrides_to_revert = []

        if override_id:
            override = db.query(Override).filter(Override.id == override_id).first()
            if not override:
                return SkillResult.fail(f"Override '{override_id}' not found.")
            overrides_to_revert.append(override)
        else:
            line_item_name = params.get("line_item_name", "")
            actor = resolve_skill_user(context)
            line_item = (
                scoped_line_items(db, actor)
                .filter(
                    (LineItem.name.ilike(f"%{line_item_name}%"))
                    | (LineItem.account_code.ilike(f"%{line_item_name}%"))
                )
                .first()
            )
            if not line_item:
                return SkillResult.fail(f"Line item '{line_item_name}' not found.")

            overrides_to_revert = (
                db.query(Override)
                .filter(
                    Override.version_id == version_id,
                    Override.line_item_id == line_item.id,
                    Override.status == "active",
                )
                .all()
            )

        if not overrides_to_revert:
            return SkillResult.fail("No active overrides found to revert.")

        actor = resolve_skill_user(context)
        # Minimal user-like object for service (id + username)
        class _Actor:
            def __init__(self, u):
                self.id = u.id if u else context.user_id
                self.username = getattr(u, "username", None) or context.user_id or "system"

        user = _Actor(actor)
        reverted_periods = []
        total_recalc = 0

        for override in overrides_to_revert:
            try:
                result = revert_override(db, override.id, user)
                total_recalc += result.get("downstream_recalc", 0)
                reverted_periods.append(override.period)
            except Exception as e:
                return SkillResult.fail(str(getattr(e, "detail", None) or e))

        line_item = db.query(LineItem).filter(LineItem.id == overrides_to_revert[0].line_item_id).first()

        content_blocks = [
            self._text_block(
                f"Reverted **{len(overrides_to_revert)}** override(s) on **{line_item.name if line_item else 'Unknown'}** "
                f"for periods: {', '.join(reverted_periods)}"
            ),
        ]

        if total_recalc > 0:
            content_blocks.append(
                self._text_block(f"Recalculated **{total_recalc}** downstream dependent lines.")
            )

        return SkillResult.ok(
            message=f"Reverted {len(overrides_to_revert)} override(s), {total_recalc} downstream recalculations",
            data={"reverted": len(overrides_to_revert), "recalculated": total_recalc},
            content_blocks=content_blocks,
        )

    async def _list_overrides(
        self, db: Session, params: dict[str, Any], context: SkillContext
    ) -> SkillResult:
        """List all active overrides for a version."""
        version_id = params.get("version_id") or context.context_manager.get_active_version_id()
        if not version_id:
            return SkillResult.fail("No active forecast version.")

        overrides = (
            db.query(Override)
            .filter(Override.version_id == version_id, Override.status == "active")
            .all()
        )

        if not overrides:
            return SkillResult.ok(
                message="No active overrides in the current forecast version.",
                data={"overrides": []},
                content_blocks=[
                    self._text_block("No active overrides in this forecast version.")
                ],
            )

        rows = []
        for o in overrides:
            line_item = db.query(LineItem).filter(LineItem.id == o.line_item_id).first()
            change = ((o.override_value - o.original_model_value) / abs(o.original_model_value) * 100) if o.original_model_value != 0 else 0
            rows.append({
                "line_item": line_item.name if line_item else "Unknown",
                "period": o.period,
                "original": f"${o.original_model_value:,.0f}",
                "override": f"${o.override_value:,.0f}",
                "change": f"{'+' if change > 0 else ''}{change:.1f}%",
                "reason": o.reason[:50] + ("..." if len(o.reason) > 50 else ""),
                "carry": "Yes" if o.carry_forward else "No",
            })

        content_blocks = [
            self._table_block(
                title=f"Active Overrides ({len(overrides)})",
                columns=[
                    {"key": "line_item", "label": "Line Item"},
                    {"key": "period", "label": "Period"},
                    {"key": "original", "label": "Model Value"},
                    {"key": "override", "label": "Override"},
                    {"key": "change", "label": "Change"},
                    {"key": "reason", "label": "Reason"},
                    {"key": "carry", "label": "Carry Fwd"},
                ],
                rows=rows,
            ),
        ]

        return SkillResult.ok(
            message=f"{len(overrides)} active override(s) in the current forecast.",
            data={"override_count": len(overrides)},
            content_blocks=content_blocks,
        )
