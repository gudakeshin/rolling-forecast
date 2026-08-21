"""CollectDriverInput skill -- manages BU driver assumption submissions."""

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.domain.base_skill import BaseSkill, SkillContext, SkillResult
from app.models.driver_input import DriverFormConfig, DriverInput
from app.models.line_item import LineItem
from app.services.driver_submission import apply_driver_submission, form_field_defs

logger = logging.getLogger(__name__)


class CollectDriverInputSkill(BaseSkill):
    """Skill to manage BU driver assumption input forms and submissions."""

    @property
    def name(self) -> str:
        return "collect_driver_input"

    @property
    def description(self) -> str:
        return (
            "Manage business unit driver assumption inputs. Can create input forms, "
            "show pending forms for a BU, submit driver values, or check submission status. "
            "Driver inputs represent business assumptions (e.g., expected deal count, "
            "hiring plans, marketing spend) that feed into the forecast. "
            "Use when the user wants to submit BU assumptions, check pending forms, "
            "or manage driver input workflows."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "Action: create_form, list_forms, show_form, submit_values, check_status",
                    "enum": ["create_form", "list_forms", "show_form", "submit_values", "check_status"],
                },
                "business_unit": {
                    "type": "string",
                    "description": "Business unit name (e.g., 'North America', 'EMEA')",
                },
                "form_id": {
                    "type": "integer",
                    "description": "Form config ID (for show_form or submit_values)",
                },
                "values": {
                    "type": "object",
                    "description": "Driver values to submit: {field_name: {value, reason}}",
                },
                "version_id": {
                    "type": "string",
                    "description": "Forecast version ID (uses active version if not specified)",
                },
                "form_name": {
                    "type": "string",
                    "description": "Name for a new form (for create_form action)",
                },
                "fields": {
                    "type": "array",
                    "description": "Field definitions for create_form: [{name, label, type, line_item_name}]",
                    "items": {"type": "object"},
                },
            },
            "required": ["action"],
        }

    @property
    def required_role(self) -> str:
        return "override"

    async def execute(self, params: dict[str, Any], context: SkillContext) -> SkillResult:
        db: Session = context.db
        action = params.get("action", "list_forms")

        if action == "create_form":
            return await self._create_form(db, params, context)
        elif action == "list_forms":
            return await self._list_forms(db, params, context)
        elif action == "show_form":
            return await self._show_form(db, params, context)
        elif action == "submit_values":
            return await self._submit_values(db, params, context)
        elif action == "check_status":
            return await self._check_status(db, params, context)
        else:
            return SkillResult.fail(f"Unknown action: {action}")

    async def _create_form(
        self, db: Session, params: dict[str, Any], context: SkillContext
    ) -> SkillResult:
        """Create a new driver input form configuration."""
        business_unit = params.get("business_unit")
        form_name = params.get("form_name")
        fields = params.get("fields", [])

        if not business_unit:
            return SkillResult.fail("Business unit is required.")
        if not form_name:
            return SkillResult.fail("Form name is required.")
        if not fields:
            return SkillResult.fail("At least one field definition is required.")

        # Resolve line item names to IDs
        enriched_fields = []
        for f in fields:
            field_def = {
                "name": f.get("name", f.get("label", "").lower().replace(" ", "_")),
                "label": f.get("label", f.get("name", "")),
                "type": f.get("type", "number"),
                "validation_rules": f.get("validation_rules", {}),
            }

            # Try to link to a line item
            line_item_name = f.get("line_item_name")
            if line_item_name:
                li = (
                    db.query(LineItem)
                    .filter(LineItem.name.ilike(f"%{line_item_name}%"))
                    .first()
                )
                if li:
                    field_def["line_item_id"] = li.id
                    field_def["line_item_name"] = li.name

            enriched_fields.append(field_def)

        form = DriverFormConfig(
            business_unit=business_unit,
            name=form_name,
            description=f"Driver assumptions form for {business_unit}",
            fields_schema={"fields": enriched_fields},
        )
        db.add(form)
        db.commit()

        content_blocks = [
            self._text_block(
                f"Created driver form **{form_name}** for **{business_unit}** "
                f"with {len(enriched_fields)} fields."
            ),
            self._table_block(
                title="Form Fields",
                columns=[
                    {"key": "name", "label": "Field"},
                    {"key": "type", "label": "Type"},
                    {"key": "linked", "label": "Linked Line Item"},
                ],
                rows=[
                    {
                        "name": f["label"],
                        "type": f["type"],
                        "linked": f.get("line_item_name", "Not linked"),
                    }
                    for f in enriched_fields
                ],
            ),
        ]

        return SkillResult.ok(
            message=f"Created driver form '{form_name}' for {business_unit} with {len(enriched_fields)} fields",
            data={"form_id": form.id},
            content_blocks=content_blocks,
        )

    async def _list_forms(
        self, db: Session, params: dict[str, Any], context: SkillContext
    ) -> SkillResult:
        """List available driver input forms."""
        query = db.query(DriverFormConfig).filter(DriverFormConfig.is_active == True)
        bu = params.get("business_unit")
        if bu:
            query = query.filter(DriverFormConfig.business_unit.ilike(f"%{bu}%"))

        forms = query.all()

        if not forms:
            return SkillResult.ok(
                message="No driver input forms configured.",
                content_blocks=[self._text_block("No driver input forms found. Create one first.")],
            )

        rows = [
            {
                "id": str(f.id),
                "name": f.name,
                "bu": f.business_unit,
                "fields": str(len(form_field_defs(f))),
                "deadline": f"Soft: {f.soft_deadline_days}d / Hard: {f.hard_deadline_days}d",
            }
            for f in forms
        ]

        return SkillResult.ok(
            message=f"Found {len(forms)} driver input form(s).",
            data={"forms": [{"id": f.id, "name": f.name} for f in forms]},
            content_blocks=[
                self._table_block(
                    title="Driver Input Forms",
                    columns=[
                        {"key": "id", "label": "ID"},
                        {"key": "name", "label": "Form"},
                        {"key": "bu", "label": "Business Unit"},
                        {"key": "fields", "label": "# Fields"},
                        {"key": "deadline", "label": "Deadlines"},
                    ],
                    rows=rows,
                ),
            ],
        )

    async def _show_form(
        self, db: Session, params: dict[str, Any], context: SkillContext
    ) -> SkillResult:
        """Show a specific form with current model-suggested values."""
        form_id = params.get("form_id")
        if not form_id:
            return SkillResult.fail("Form ID is required.")

        form = db.query(DriverFormConfig).filter(DriverFormConfig.id == form_id).first()
        if not form:
            return SkillResult.fail(f"Form {form_id} not found.")

        version_id = params.get("version_id") or context.context_manager.get_active_version_id()

        from app.models.forecast import ForecastLineResult

        fields = form_field_defs(form)
        rows = []
        for field in fields:
            suggested = "-"
            if version_id and field.get("line_item_id"):
                # Get model-suggested value
                result = (
                    db.query(ForecastLineResult)
                    .filter(
                        ForecastLineResult.version_id == version_id,
                        ForecastLineResult.line_item_id == field["line_item_id"],
                    )
                    .first()
                )
                if result:
                    suggested = f"${result.p50:,.0f}"

            rows.append({
                "field": field.get("label", field.get("name", "")),
                "type": field.get("type", "number"),
                "model_suggested": suggested,
                "linked_item": field.get("line_item_name", "N/A"),
            })

        content_blocks = [
            self._text_block(
                f"**{form.name}** — {form.business_unit}\n"
                f"Submit your assumptions for the current forecast cycle."
            ),
            self._table_block(
                title="Input Fields",
                columns=[
                    {"key": "field", "label": "Driver"},
                    {"key": "type", "label": "Type"},
                    {"key": "model_suggested", "label": "Model Suggested"},
                    {"key": "linked_item", "label": "Linked Line"},
                ],
                rows=rows,
            ),
        ]

        return SkillResult.ok(
            message=f"Form '{form.name}' for {form.business_unit}: {len(fields)} fields",
            data={"form_id": form.id, "fields": fields},
            content_blocks=content_blocks,
        )

    async def _submit_values(
        self, db: Session, params: dict[str, Any], context: SkillContext
    ) -> SkillResult:
        """Submit driver values and apply them to the forecast."""
        form_id = params.get("form_id")
        values = params.get("values", {})
        version_id = params.get("version_id") or context.context_manager.get_active_version_id()

        if not form_id:
            return SkillResult.fail("Form ID is required.")
        if not values:
            return SkillResult.fail("No values provided to submit.")
        if not version_id:
            return SkillResult.fail("No active forecast version.")

        form = db.query(DriverFormConfig).filter(DriverFormConfig.id == form_id).first()
        if not form:
            return SkillResult.fail(f"Form {form_id} not found.")

        fields_map = {f["name"]: f for f in form_field_defs(form)}

        try:
            result = apply_driver_submission(
                db,
                version_id=version_id,
                form=form,
                values=values,
                user_id=context.user_id,
                business_unit=form.business_unit,
                apply_overrides=True,
                actor_username=None,
                audit=True,
                commit=True,
            )
        except ValueError as e:
            return SkillResult.fail(str(e))

        enriched_values = result.enriched_values
        applied_count = result.overrides_applied
        deadline_status = result.deadline_status

        content_blocks = [
            self._text_block(
                f"Submitted driver assumptions for **{form.business_unit}** ({form.name})"
            ),
            self._table_block(
                title="Submitted Values",
                columns=[
                    {"key": "field", "label": "Driver"},
                    {"key": "value", "label": "Your Value"},
                    {"key": "model", "label": "Model Suggested"},
                    {"key": "status", "label": "Applied"},
                ],
                rows=[
                    {
                        "field": fields_map.get(k, {}).get("label", k),
                        "value": f"${v['value']:,.0f}" if isinstance(v.get("value"), (int, float)) else str(v.get("value")),
                        "model": f"${v.get('model_suggested', 0):,.0f}" if v.get("model_suggested") else "-",
                        "status": "Yes" if abs(v.get("value", 0) - v.get("model_suggested", v.get("value", 0))) > 0.01 else "No change",
                    }
                    for k, v in enriched_values.items()
                ],
            ),
        ]

        if applied_count > 0:
            content_blocks.append(
                self._text_block(
                    f"Applied **{applied_count}** value(s) as overrides to the forecast with downstream recalculation."
                )
            )

        if deadline_status.get("is_past_hard"):
            content_blocks.append(
                self._text_block(
                    f"**Late Submission:** {deadline_status['message']}"
                )
            )
        elif deadline_status.get("is_past_soft"):
            content_blocks.append(
                self._text_block(
                    f"**Deadline Warning:** {deadline_status['message']}"
                )
            )

        return SkillResult.ok(
            message=(
                f"Submitted {len(enriched_values)} driver values for {form.business_unit}, "
                f"{applied_count} applied as overrides"
            ),
            data={
                "submission_id": result.driver_input.id,
                "values_count": len(enriched_values),
                "overrides_applied": applied_count,
            },
            content_blocks=content_blocks,
        )

    async def _check_status(
        self, db: Session, params: dict[str, Any], context: SkillContext
    ) -> SkillResult:
        """Check submission status across business units."""
        version_id = params.get("version_id") or context.context_manager.get_active_version_id()
        if not version_id:
            return SkillResult.fail("No active forecast version.")

        # Get all forms
        forms = db.query(DriverFormConfig).filter(DriverFormConfig.is_active == True).all()

        # Get submissions for this version
        submissions = (
            db.query(DriverInput)
            .filter(DriverInput.version_id == version_id)
            .all()
        )
        submissions_by_form: dict[int, list[Any]] = {}
        for s in submissions:
            if s.form_config_id not in submissions_by_form:
                submissions_by_form[s.form_config_id] = []
            submissions_by_form[s.form_config_id].append(s)

        rows = []
        for form in forms:
            subs = submissions_by_form.get(form.id, [])
            latest = max(subs, key=lambda s: s.submitted_at) if subs else None
            rows.append({
                "form": form.name,
                "bu": form.business_unit,
                "status": latest.status if latest else "Not submitted",
                "submitted_by": "N/A" if not latest else (latest.user_id[:8] + "..."),
                "submitted_at": latest.submitted_at.strftime("%Y-%m-%d %H:%M") if latest else "-",
                "is_late": "Late" if latest and latest.is_late else ("On time" if latest else "-"),
            })

        submitted_count = sum(1 for r in rows if r["status"] != "Not submitted")

        content_blocks = [
            self._text_block(
                f"**Driver Input Status**: {submitted_count}/{len(forms)} forms submitted"
            ),
            self._table_block(
                title="Submission Status",
                columns=[
                    {"key": "form", "label": "Form"},
                    {"key": "bu", "label": "BU"},
                    {"key": "status", "label": "Status"},
                    {"key": "submitted_at", "label": "Submitted"},
                    {"key": "is_late", "label": "Timeliness"},
                ],
                rows=rows,
            ),
        ]

        return SkillResult.ok(
            message=f"Driver input status: {submitted_count}/{len(forms)} submitted",
            data={
                "total_forms": len(forms),
                "submitted": submitted_count,
                "pending": len(forms) - submitted_count,
            },
            content_blocks=content_blocks,
        )
