"""Shared driver-input submission — persist + apply overrides + DAG recalc.

Both the collect_driver_input skill and the REST /panel/driver-inputs/submit
endpoint must take this path so UI submits actually land on forecast lines.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models.driver_input import DriverFormConfig, DriverInput
from app.models.forecast import ForecastLineResult
from app.services.audit import record_audit
from app.services.dependency_graph import DependencyGraphManager
from app.services.error_handlers import check_driver_deadline


@dataclass
class DriverSubmissionResult:
    driver_input: DriverInput
    overrides_applied: int
    enriched_values: dict[str, Any]
    is_late: bool
    deadline_status: dict[str, Any]


def form_field_defs(form: DriverFormConfig) -> list[dict[str, Any]]:
    """Normalize fields_schema (dict-with-fields or bare list) to a field list."""
    schema = form.fields_schema
    if isinstance(schema, list):
        return [f for f in schema if isinstance(f, dict)]
    if isinstance(schema, dict):
        fields = schema.get("fields")
        if isinstance(fields, list):
            return [f for f in fields if isinstance(f, dict)]
        # Legacy / auto-created shapes sometimes store fields at top level
        if "name" in schema:
            return [schema]
    return []


def _resolve_line_item_id(
    key: str,
    field_def: dict[str, Any] | None,
    payload: dict[str, Any] | None,
) -> int | None:
    """Resolve line_item_id from numeric key, field def, or payload."""
    if field_def and field_def.get("line_item_id") is not None:
        try:
            return int(field_def["line_item_id"])
        except (TypeError, ValueError):
            pass
    if payload and payload.get("line_item_id") is not None:
        try:
            return int(payload["line_item_id"])
        except (TypeError, ValueError):
            pass
    if str(key).isdigit():
        return int(key)
    return None


def apply_driver_submission(
    db: Session,
    *,
    version_id: str,
    form: DriverFormConfig,
    values: dict[str, Any],
    user_id: str,
    business_unit: str | None = None,
    notes: str | None = None,
    apply_overrides: bool = True,
    actor_username: str | None = None,
    audit: bool = True,
    commit: bool = True,
) -> DriverSubmissionResult:
    """Persist a driver submission and optionally apply values as overrides.

    ``values`` keys may be form field names (skill path) or string line-item
    ids (REST/UI path). Numeric keys are treated as line_item_id.
    """
    fields = form_field_defs(form)
    fields_by_name = {str(f.get("name")): f for f in fields if f.get("name") is not None}

    enriched_values: dict[str, Any] = {}
    applied_count = 0
    dag = DependencyGraphManager(db) if apply_overrides else None

    for key, submission in (values or {}).items():
        if str(key).startswith("_"):
            continue

        payload = submission if isinstance(submission, dict) else {"value": submission}
        value = payload.get("value")
        if value is None:
            continue
        try:
            numeric_value = float(value)
        except (TypeError, ValueError):
            continue

        field_def = fields_by_name.get(str(key))
        line_item_id = _resolve_line_item_id(str(key), field_def, payload)

        enriched: dict[str, Any] = {
            "value": numeric_value,
            "source": payload.get("source", "manual"),
            "reason": payload.get("reason") or notes or "Driver input submission",
        }
        if line_item_id is not None:
            enriched["line_item_id"] = line_item_id

        if line_item_id is not None and apply_overrides and dag is not None:
            results = (
                db.query(ForecastLineResult)
                .filter(
                    ForecastLineResult.version_id == version_id,
                    ForecastLineResult.line_item_id == line_item_id,
                )
                .all()
            )
            for result in results:
                enriched["model_suggested"] = result.p50
                enriched["prior_value"] = result.p50
                if abs(numeric_value - (result.p50 or 0.0)) > 0.01:
                    result.is_overridden = True
                    result.override_value = numeric_value
                    dag.recalculate_dependents(version_id, line_item_id, [result.period])
                    applied_count += 1

        enriched_values[str(key)] = enriched

    if not enriched_values:
        raise ValueError("No driver values provided")

    deadline_status = check_driver_deadline(
        soft_deadline_days=form.soft_deadline_days,
        hard_deadline_days=form.hard_deadline_days,
    )
    is_late = bool(deadline_status.get("is_past_hard"))
    submission_status = "late" if is_late else "submitted"

    bu = business_unit or form.business_unit or "Default"
    driver_input = DriverInput(
        version_id=version_id,
        form_config_id=form.id,
        user_id=user_id,
        business_unit=bu,
        values=enriched_values,
        status=submission_status,
        submitted_at=datetime.now(timezone.utc),
        is_late=is_late,
    )
    db.add(driver_input)

    if audit:
        record_audit(
            db,
            action="driver.submit",
            entity_type="driver_input",
            entity_id=None,
            actor_id=user_id,
            actor_username=actor_username,
            details={
                "version_id": version_id,
                "business_unit": bu,
                "field_count": len(enriched_values),
                "overrides_applied": applied_count,
            },
        )

    if commit:
        db.commit()
        db.refresh(driver_input)
    else:
        db.flush()

    return DriverSubmissionResult(
        driver_input=driver_input,
        overrides_applied=applied_count,
        enriched_values=enriched_values,
        is_late=is_late,
        deadline_status=deadline_status,
    )
