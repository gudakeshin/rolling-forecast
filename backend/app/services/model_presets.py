"""Saved model presets — named shortcuts over generate_baseline's model params.

A preset is purely a convenience layer: it never changes the forecasting
engine, and its model_type/candidate_models are validated against the live
model registry only at *resolution* time (when a run actually uses it), not
at create/update time, since registry membership is env-flag-dependent
(e.g. ``global_gbm`` only exists when ``settings.enable_global_gbm_model``
is on).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.domain.engines.model_registry import get_model_registry, reset_model_registry
from app.models.model_preset import ModelPreset
from app.models.user import User
from app.services.audit import record_audit


def _find_by_name(db: Session, name: str, *, exclude_id: str | None = None) -> ModelPreset | None:
    query = db.query(ModelPreset).filter(func.lower(ModelPreset.name) == name.strip().lower())
    if exclude_id is not None:
        query = query.filter(ModelPreset.id != exclude_id)
    return query.first()


def create_preset(
    db: Session,
    *,
    name: str,
    description: str | None,
    model_type: str = "auto",
    candidate_models: list[str] | None = None,
    default_horizon_months: int | None = None,
    actor: User | None = None,
) -> ModelPreset:
    name = (name or "").strip()
    if not name:
        raise ValueError("Preset name is required")
    if candidate_models and model_type != "auto":
        raise ValueError(
            "candidate_models only applies when model_type is 'auto' "
            "(it restricts the auto-selection pool; a pinned model_type ignores it)"
        )
    if _find_by_name(db, name) is not None:
        raise ValueError(f"A model preset named '{name}' already exists")

    preset = ModelPreset(
        name=name,
        description=description,
        model_type=model_type or "auto",
        candidate_models=candidate_models or None,
        default_horizon_months=default_horizon_months,
        created_by=actor.id if actor else None,
    )
    db.add(preset)
    db.flush()
    if actor is not None:
        record_audit(
            db,
            action="model_preset.create",
            entity_type="model_preset",
            entity_id=preset.id,
            actor_id=actor.id,
            actor_username=actor.username,
            details={"name": preset.name, "model_type": preset.model_type},
        )
    db.commit()
    db.refresh(preset)
    return preset


def list_presets(db: Session, *, include_inactive: bool = False) -> list[ModelPreset]:
    query = db.query(ModelPreset)
    if not include_inactive:
        query = query.filter(ModelPreset.is_active.is_(True))
    return query.order_by(ModelPreset.name).all()


def get_preset(db: Session, preset_id: str) -> ModelPreset | None:
    return db.query(ModelPreset).filter(ModelPreset.id == preset_id).first()


def find_preset(db: Session, preset_id_or_name: str) -> ModelPreset | None:
    """Look up a preset by id first, falling back to a case-insensitive name match.

    Chat callers naturally refer to presets by name; REST/UI callers pass the id.
    """
    preset = get_preset(db, preset_id_or_name)
    if preset is not None:
        return preset
    return _find_by_name(db, preset_id_or_name)


def update_preset(
    db: Session,
    preset_id: str,
    *,
    actor: User | None = None,
    **fields: Any,
) -> ModelPreset:
    preset = get_preset(db, preset_id)
    if preset is None:
        raise ValueError(f"Model preset '{preset_id}' not found")

    if "name" in fields and fields["name"] is not None:
        new_name = fields["name"].strip()
        if not new_name:
            raise ValueError("Preset name cannot be empty")
        if _find_by_name(db, new_name, exclude_id=preset.id) is not None:
            raise ValueError(f"A model preset named '{new_name}' already exists")
        preset.name = new_name

    model_type = fields.get("model_type", preset.model_type)
    candidate_models = (
        fields["candidate_models"] if "candidate_models" in fields else preset.candidate_models
    )
    if candidate_models and model_type != "auto":
        raise ValueError(
            "candidate_models only applies when model_type is 'auto' "
            "(it restricts the auto-selection pool; a pinned model_type ignores it)"
        )

    if "description" in fields:
        preset.description = fields["description"]
    if "model_type" in fields and fields["model_type"] is not None:
        preset.model_type = fields["model_type"]
    if "candidate_models" in fields:
        preset.candidate_models = fields["candidate_models"] or None
    if "default_horizon_months" in fields:
        preset.default_horizon_months = fields["default_horizon_months"]

    db.flush()
    if actor is not None:
        record_audit(
            db,
            action="model_preset.update",
            entity_type="model_preset",
            entity_id=preset.id,
            actor_id=actor.id,
            actor_username=actor.username,
            details={"name": preset.name, "model_type": preset.model_type},
        )
    db.commit()
    db.refresh(preset)
    return preset


def deactivate_preset(db: Session, preset_id: str, actor: User | None = None) -> ModelPreset:
    preset = get_preset(db, preset_id)
    if preset is None:
        raise ValueError(f"Model preset '{preset_id}' not found")
    if not preset.is_active:
        return preset

    preset.is_active = False
    db.flush()
    if actor is not None:
        record_audit(
            db,
            action="model_preset.deactivate",
            entity_type="model_preset",
            entity_id=preset.id,
            actor_id=actor.id,
            actor_username=actor.username,
            details={"name": preset.name},
        )
    db.commit()
    db.refresh(preset)
    return preset


def resolve_preset(
    db: Session,
    preset_id_or_name: str,
    *,
    override_horizon: int | None = None,
) -> dict[str, Any]:
    """Resolve a saved preset into generate_baseline kwargs.

    Validates against the *live* model registry (rebuilt fresh, same as
    generate_baseline does before every run) — never at create/update time,
    since registry membership depends on settings flags that can change
    independently of when a preset was saved.
    """
    preset = find_preset(db, preset_id_or_name)
    if preset is None:
        raise ValueError(f"Model preset '{preset_id_or_name}' not found")
    if not preset.is_active:
        raise ValueError(f"Model preset '{preset.name}' has been deactivated")

    reset_model_registry()
    registry = get_model_registry()
    registered = set(registry.list_models())

    warnings: list[str] = []
    model_type = preset.model_type
    if model_type != "auto" and model_type not in registered:
        raise ValueError(
            f"Model preset '{preset.name}' pins model '{model_type}', which is not "
            f"currently registered (disabled by a feature flag, or removed). "
            f"Currently registered models: {sorted(registered)}"
        )

    models_to_test = None
    if preset.candidate_models:
        available = [m for m in preset.candidate_models if m in registered]
        missing = [m for m in preset.candidate_models if m not in registered]
        if missing:
            warnings.append(
                f"Preset '{preset.name}' references unregistered models {missing}; "
                "they were dropped from the candidate pool for this run."
            )
        models_to_test = available or None

    return {
        "preset_id": preset.id,
        "preset_name": preset.name,
        "model_type": model_type,
        "models_to_test": models_to_test,
        "horizon_months": override_horizon or preset.default_horizon_months,
        "warnings": warnings,
    }


class _StubContextManager:
    """Minimal context manager for non-chat (REST-triggered) skill execution.

    Mirrors app.workers.arq_worker._StubContextManager — duplicated rather
    than imported to avoid pulling the arq worker module (and its `arq`
    dependency) into the API process for a plain REST request.
    """

    def __init__(self, user_id: str):
        self._mem: dict[str, Any] = {}
        self._user_id = user_id

    def get_memory(self, key: str, default: Any = None) -> Any:
        return self._mem.get(key, default)

    def set_memory(self, key: str, value: Any) -> None:
        self._mem[key] = value

    def get_active_version_id(self) -> str | None:
        return self._mem.get("active_version_id")

    def set_active_version_id(self, version_id: str) -> None:
        self._mem["active_version_id"] = version_id


async def run_forecast_with_preset(
    db: Session,
    *,
    preset_id_or_name: str,
    dataset_id: str | None = None,
    horizon_months: int | None = None,
    scenario: str = "base",
    async_job: bool = True,
    user_id: str | None,
    user_role: str = "generate",
    conversation_id: str | None = None,
) -> dict[str, Any]:
    """Resolve a preset and run generate_baseline with it.

    Delegates to GenerateBaselineSkill().execute() so the existing
    enqueue-vs-synchronous-fallback logic (see generate_baseline.py) is
    reused as-is rather than reimplemented here. The single entry point
    both the REST "Run Forecast" panel and the run_forecast_with_model
    chat skill resolve a preset through (resolve_preset), though chat runs
    execute() with its own live SkillContext instead of the stub below.
    """
    from app.domain.base_skill import SkillContext
    from app.domain.skills.generate_baseline import GenerateBaselineSkill

    resolved = resolve_preset(db, preset_id_or_name, override_horizon=horizon_months)
    params: dict[str, Any] = {
        "model_type": resolved["model_type"],
        "models_to_test": resolved["models_to_test"],
        "scenario": scenario,
        "async_job": async_job,
        "model_preset_id": resolved["preset_id"],
    }
    if resolved["horizon_months"] is not None:
        params["horizon_months"] = resolved["horizon_months"]
    if dataset_id is not None:
        params["dataset_id"] = dataset_id

    uid = user_id or "system"
    context = SkillContext(
        db=db,
        context_manager=_StubContextManager(uid),  # type: ignore[arg-type]
        user_id=uid,
        user_role=user_role,
        conversation_id=conversation_id or f"model-preset-run-{resolved['preset_id']}",
    )
    result = await GenerateBaselineSkill().execute(params, context)
    return {
        "success": result.success,
        "message": result.message,
        "data": result.data,
        "warnings": resolved["warnings"],
    }
