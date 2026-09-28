"""Per-company overrides of the forecast-behavior knobs in app.config.Settings.

Built on the same additive-override pattern as app.services.tenant_settings
(reporting_currency / fiscal_calendar from Phase 2 pass 2b): each field here
is stored as a `BusinessUnitSetting` row keyed `analysis.<field>`, and reads
fall through to the global `app.config.settings` default whenever no
per-business-unit override exists. No new table or migration -- the existing
generic key/value override table is typed enough for this.

Deliberately excluded (a named, deferred gap -- not silently skipped):
`enable_benchmark_models` and `enable_global_gbm_model` gate which models get
*registered* inside `ModelRegistry.__init__` (app/domain/engines/model_registry.py),
a process-wide singleton built once per process, not read at selection time.
Making those two per-company means moving the gate from construction-time to
selection-time (or building a registry per scope) -- a distinct, riskier
change left for a follow-up pass. Every field below is read per-call (or
already accepts a per-call override, e.g. `selection_rule` /
`wall_clock_budget_seconds` on `ModelRegistry.compare_models`), so it wires
through cleanly today.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.config import settings
from app.services.audit import record_audit
from app.services.tenant_settings import get_tenant_setting, set_tenant_setting

# name -> python type. Order matches app/config.py's "Forecast defaults" block.
ANALYSIS_PROFILE_FIELDS: dict[str, type] = {
    "default_horizon_months": int,
    "confidence_threshold_low": int,
    "confidence_threshold_medium": int,
    "selection_metric": str,
    "selection_wall_clock_seconds": float,
    "materiality_share": float,
    "qp_coherence_tolerance": float,
    "enable_driver_forecasting": bool,
    "exog_min_points_per_regressor": int,
    "min_history_months": int,
    "ideal_history_months": int,
    "outlier_cleaning_enabled": bool,
    "outlier_mad_z": float,
    "structural_break_min_post_points": int,
    "conformal_calibration_enabled": bool,
    "conformal_min_residuals_per_horizon": int,
    "conformal_min_residuals_total": int,
    "conformal_realized_min_cycles": int,
    "enable_ensemble_blending": bool,
}

_KEY_PREFIX = "analysis."


@dataclass(frozen=True)
class AnalysisSettings:
    default_horizon_months: int
    confidence_threshold_low: int
    confidence_threshold_medium: int
    selection_metric: str
    selection_wall_clock_seconds: float
    materiality_share: float
    qp_coherence_tolerance: float
    enable_driver_forecasting: bool
    exog_min_points_per_regressor: int
    min_history_months: int
    ideal_history_months: int
    outlier_cleaning_enabled: bool
    outlier_mad_z: float
    structural_break_min_post_points: int
    conformal_calibration_enabled: bool
    conformal_min_residuals_per_horizon: int
    conformal_min_residuals_total: int
    conformal_realized_min_cycles: int
    enable_ensemble_blending: bool


def _decode(field_type: type, raw: str) -> Any:
    if field_type is bool:
        return raw.strip().lower() in ("1", "true", "yes", "on")
    return field_type(raw)


def _encode(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def resolve_analysis_settings(db: Session, *, business_unit_id: str | None) -> AnalysisSettings:
    """Company override, falling back to the global `app.config.settings` default.

    Safe to call with `business_unit_id=None` (e.g. a system-wide job) -- every
    field then resolves straight to the global default, identical to reading
    `settings.<field>` directly today."""
    values: dict[str, Any] = {}
    for name, field_type in ANALYSIS_PROFILE_FIELDS.items():
        raw = get_tenant_setting(db, f"{_KEY_PREFIX}{name}", business_unit_id=business_unit_id)
        values[name] = _decode(field_type, raw) if raw is not None else getattr(settings, name)
    return AnalysisSettings(**values)


def get_analysis_profile_overrides(db: Session, *, business_unit_id: str) -> dict[str, Any]:
    """Only the fields actually overridden for this business unit (global-default
    fields are omitted) -- for an admin UI to show "custom" vs "inherited"."""
    overrides: dict[str, Any] = {}
    for name, field_type in ANALYSIS_PROFILE_FIELDS.items():
        raw = get_tenant_setting(db, f"{_KEY_PREFIX}{name}", business_unit_id=business_unit_id)
        if raw is not None:
            overrides[name] = _decode(field_type, raw)
    return overrides


def update_analysis_profile(
    db: Session,
    *,
    business_unit_id: str,
    patch: dict[str, Any],
    actor,
) -> AnalysisSettings:
    """Validate and write a set of per-business-unit overrides, audited one
    event per changed key. Raises ValueError for an unknown key or a value
    that doesn't match its declared type."""
    unknown = set(patch) - set(ANALYSIS_PROFILE_FIELDS)
    if unknown:
        raise ValueError(f"Unknown analysis profile field(s): {sorted(unknown)}")

    for name, value in patch.items():
        field_type = ANALYSIS_PROFILE_FIELDS[name]
        if field_type is bool and not isinstance(value, bool):
            raise ValueError(f"'{name}' must be a boolean")
        if field_type in (int, float) and isinstance(value, bool):
            raise ValueError(f"'{name}' must be a number")
        if field_type in (int, float) and not isinstance(value, (int, float)):
            raise ValueError(f"'{name}' must be a number")
        coerced = field_type(value)
        set_tenant_setting(
            db, f"{_KEY_PREFIX}{name}", _encode(coerced), business_unit_id=business_unit_id
        )
        record_audit(
            db,
            action="analysis_profile.update",
            entity_type="business_unit",
            entity_id=business_unit_id,
            actor_id=getattr(actor, "id", None),
            actor_username=getattr(actor, "username", None),
            details={"field": name, "value": coerced},
        )

    return resolve_analysis_settings(db, business_unit_id=business_unit_id)
