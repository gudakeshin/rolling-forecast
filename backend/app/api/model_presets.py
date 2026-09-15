"""Model preset APIs — manage named model configurations and run forecasts with them."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.domain.engines.model_registry import get_model_registry, reset_model_registry
from app.models.model_preset import ModelPreset
from app.models.user import User
from app.services.model_presets import (
    create_preset,
    deactivate_preset,
    get_preset,
    list_presets,
    run_forecast_with_preset,
    update_preset,
)
from app.services.permissions import require_permission

router = APIRouter(prefix="/model-presets", tags=["model-presets"])


class ModelPresetCreate(BaseModel):
    name: str
    description: str | None = None
    model_type: str = "auto"
    candidate_models: list[str] | None = None
    default_horizon_months: int | None = Field(None, ge=1, le=60)


class ModelPresetUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    model_type: str | None = None
    candidate_models: list[str] | None = None
    default_horizon_months: int | None = Field(None, ge=1, le=60)


class RunForecastRequest(BaseModel):
    dataset_id: str | None = None
    business_unit: str | None = None
    horizon_months: int | None = Field(None, ge=1, le=60)
    scenario: str = "base"
    async_job: bool = True


def _serialize(preset: ModelPreset) -> dict:
    return {
        "id": preset.id,
        "name": preset.name,
        "description": preset.description,
        "model_type": preset.model_type,
        "candidate_models": preset.candidate_models,
        "default_horizon_months": preset.default_horizon_months,
        "is_active": preset.is_active,
        "created_at": preset.created_at.isoformat() if preset.created_at else None,
        "created_by": preset.created_by,
        "updated_at": preset.updated_at.isoformat() if preset.updated_at else None,
    }


@router.get("/available-models")
async def available_models(
    current_user: User = Depends(require_permission("generate")),
):
    """Registry models a preset can pin/restrict to, for the preset-builder UI."""
    reset_model_registry()
    registry = get_model_registry()
    out = []
    for name in registry.list_models():
        model = registry.get(name)
        if model is None:
            continue
        caps = model.capabilities
        out.append(
            {
                "name": name,
                "display_label": caps.display_label or name,
                "auto_selectable": caps.auto_selectable,
                "is_benchmark": caps.is_benchmark,
                "cost_class": caps.cost_class,
            }
        )
    return {"models": out}


@router.post("", status_code=201)
async def create_model_preset(
    body: ModelPresetCreate,
    current_user: User = Depends(require_permission("admin")),
    db: Session = Depends(get_db),
):
    try:
        preset = create_preset(
            db,
            name=body.name,
            description=body.description,
            model_type=body.model_type,
            candidate_models=body.candidate_models,
            default_horizon_months=body.default_horizon_months,
            actor=current_user,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return _serialize(preset)


@router.get("")
async def list_model_presets(
    include_inactive: bool = False,
    current_user: User = Depends(require_permission("generate")),
    db: Session = Depends(get_db),
):
    rows = list_presets(db, include_inactive=include_inactive)
    return {"presets": [_serialize(r) for r in rows], "count": len(rows)}


@router.get("/{preset_id}")
async def get_model_preset(
    preset_id: str,
    current_user: User = Depends(require_permission("generate")),
    db: Session = Depends(get_db),
):
    preset = get_preset(db, preset_id)
    if preset is None:
        raise HTTPException(404, "Model preset not found")
    return _serialize(preset)


@router.put("/{preset_id}")
async def update_model_preset(
    preset_id: str,
    body: ModelPresetUpdate,
    current_user: User = Depends(require_permission("admin")),
    db: Session = Depends(get_db),
):
    fields = body.model_dump(exclude_unset=True)
    try:
        preset = update_preset(db, preset_id, actor=current_user, **fields)
    except ValueError as e:
        msg = str(e)
        raise HTTPException(404 if "not found" in msg else 409, msg) from e
    return _serialize(preset)


@router.delete("/{preset_id}")
async def deactivate_model_preset(
    preset_id: str,
    current_user: User = Depends(require_permission("admin")),
    db: Session = Depends(get_db),
):
    try:
        preset = deactivate_preset(db, preset_id, actor=current_user)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    return {"id": preset.id, "is_active": preset.is_active}


@router.post("/{preset_id}/run")
async def run_model_preset(
    preset_id: str,
    body: RunForecastRequest,
    current_user: User = Depends(require_permission("generate")),
    db: Session = Depends(get_db),
):
    try:
        result = await run_forecast_with_preset(
            db,
            preset_id_or_name=preset_id,
            dataset_id=body.dataset_id,
            business_unit=body.business_unit,
            horizon_months=body.horizon_months,
            scenario=body.scenario,
            async_job=body.async_job,
            user_id=current_user.id,
            user_role=current_user.role.name if current_user.role else "generate",
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return result
