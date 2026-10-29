"""Skills Management API -- list, read, and edit skill .md definitions."""

import logging
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.auth import get_current_user
from app.domain.registry import get_registry
from app.domain.skill_loader import (
    load_skill_definition,
    save_skill_definition,
    list_skill_files,
    parse_skill_md,
    SKILLS_DIR,
)
from app.models.user import User
from app.services.permissions import (
    can_access_business_unit,
    require_permission,
    require_platform_admin,
)
from app.services.skill_overrides import (
    delete_skill_override,
    effective_skill_definitions,
    get_skill_override,
    set_skill_override,
)
from app.services.audit import record_audit
from app.database import get_db
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/skills", tags=["skills"])


class SkillUpdateRequest(BaseModel):
    content: str  # Full .md file content


class SkillCreateRequest(BaseModel):
    name: str
    content: str


class SkillOverrideRequest(BaseModel):
    content: str  # Override .md: frontmatter (description/tags) + optional body
    business_unit_id: str | None = None


def _override_company(user: User, business_unit_id: str | None) -> str:
    """The company an override call acts on: the one named, if the caller can
    reach it, else the caller's own. 404 (not 403) for an inaccessible one,
    so company ids can't be probed."""
    bu_id = business_unit_id or user.business_unit_id
    if not bu_id or not can_access_business_unit(user, bu_id):
        raise HTTPException(status_code=404, detail="Business unit not found")
    return bu_id


def _definition_view(defn) -> dict:
    return {
        "description": defn.description,
        "tags": defn.tags,
        "version": defn.version,
        "when_to_use": defn.when_to_use,
        "examples": defn.examples,
        "instructions": defn.instructions,
    }


@router.get("/")
async def list_skills(current_user: User = Depends(get_current_user)):
    """List all registered skills with their .md definition status."""
    registry = get_registry()
    return {
        "skills": registry.list_with_definitions(),
        "total": len(registry.list_names()),
        "skills_dir": str(SKILLS_DIR),
    }


@router.get("/definitions")
async def list_definitions(current_user: User = Depends(get_current_user)):
    """List all skill .md definition files."""
    return {
        "definitions": list_skill_files(),
        "skills_dir": str(SKILLS_DIR),
    }


@router.get("/{skill_name}")
async def get_skill(skill_name: str, current_user: User = Depends(get_current_user)):
    """Get a skill's details including its .md definition content."""
    registry = get_registry()
    skill = registry.get(skill_name)
    defn = registry.get_definition(skill_name)

    if not skill and not defn:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found")

    md_content = None
    md_file = SKILLS_DIR / f"{skill_name}.md"
    if md_file.exists():
        md_content = md_file.read_text(encoding="utf-8")

    return {
        "name": skill_name,
        "registered": skill is not None,
        "has_definition": defn is not None,
        "description": defn.description if defn else (skill.description if skill else ""),
        "parameters": defn.parameters if defn else [],
        "parameters_schema": defn.parameters_schema if defn else (
            skill.parameters_schema if skill else {}
        ),
        "required_role": defn.required_role if defn else (
            skill.required_role if skill else None
        ),
        "tags": defn.tags if defn else [],
        "version": defn.version if defn else "1.0",
        "when_to_use": defn.when_to_use if defn else "",
        "examples": defn.examples if defn else [],
        "instructions": defn.instructions if defn else "",
        "md_content": md_content,
        "md_file": str(md_file),
    }


@router.put("/{skill_name}")
async def update_skill(
    skill_name: str,
    req: SkillUpdateRequest,
    current_user: User = Depends(require_platform_admin()),
    db: Session = Depends(get_db),
):
    """Update a skill's .md definition file (admin only)."""
    try:
        defn = parse_skill_md(req.content, f"{skill_name}.md")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if defn.name != skill_name:
        raise HTTPException(
            status_code=400,
            detail=f"Skill name in .md file ('{defn.name}') doesn't match URL ('{skill_name}')"
        )

    success = save_skill_definition(skill_name, req.content)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to save skill definition")

    registry = get_registry()
    new_defn = load_skill_definition(skill_name)
    if new_defn:
        registry._definitions[skill_name] = new_defn
    registry.bump_definitions_generation()

    record_audit(
        db,
        action="skills.update",
        entity_type="skill",
        entity_id=skill_name,
        actor_id=current_user.id,
        actor_username=current_user.username,
        commit=True,
    )
    logger.info(f"Skill definition updated: {skill_name}")

    return {
        "success": True,
        "name": skill_name,
        "version": defn.version,
        "message": f"Skill '{skill_name}' definition updated successfully",
    }


@router.post("/")
async def create_skill_definition(
    req: SkillCreateRequest,
    current_user: User = Depends(require_platform_admin()),
    db: Session = Depends(get_db),
):
    """Create a new skill .md definition file (admin only)."""
    md_file = SKILLS_DIR / f"{req.name}.md"
    if md_file.exists():
        raise HTTPException(
            status_code=409, detail=f"Skill definition '{req.name}.md' already exists"
        )

    try:
        parse_skill_md(req.content, f"{req.name}.md")  # validate before save
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    success = save_skill_definition(req.name, req.content)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to create skill definition")

    record_audit(
        db,
        action="skills.create",
        entity_type="skill",
        entity_id=req.name,
        actor_id=current_user.id,
        actor_username=current_user.username,
        commit=True,
    )

    return {
        "success": True,
        "name": req.name,
        "file": str(md_file),
        "message": f"Skill definition '{req.name}' created. Add a Python class to enable execution.",
    }


@router.get("/{skill_name}/override")
async def get_skill_override_view(
    skill_name: str,
    business_unit_id: str | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """A company's override of a skill (if any) and the resulting definition."""
    if get_registry().get_definition(skill_name) is None:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found")
    bu_id = _override_company(current_user, business_unit_id)
    definitions, _ = effective_skill_definitions(db, business_unit_id=bu_id)
    return {
        "name": skill_name,
        "business_unit_id": bu_id,
        "content": get_skill_override(db, business_unit_id=bu_id, skill_name=skill_name),
        "effective": _definition_view(definitions[skill_name]),
    }


@router.put("/{skill_name}/override")
async def put_skill_override(
    skill_name: str,
    req: SkillOverrideRequest,
    current_user: User = Depends(require_permission("admin")),
    db: Session = Depends(get_db),
):
    """Set a company's override of a skill's LLM-facing text (company admin)."""
    bu_id = _override_company(current_user, req.business_unit_id)
    try:
        effective = set_skill_override(
            db, business_unit_id=bu_id, skill_name=skill_name,
            content=req.content, actor=current_user,
        )
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    db.commit()
    return {
        "name": skill_name,
        "business_unit_id": bu_id,
        "content": req.content,
        "effective": _definition_view(effective),
    }


@router.delete("/{skill_name}/override", status_code=204)
async def remove_skill_override(
    skill_name: str,
    business_unit_id: str | None = None,
    current_user: User = Depends(require_permission("admin")),
    db: Session = Depends(get_db),
):
    """Drop a company's override, reverting it to the global definition."""
    bu_id = _override_company(current_user, business_unit_id)
    if not delete_skill_override(
        db, business_unit_id=bu_id, skill_name=skill_name, actor=current_user
    ):
        raise HTTPException(status_code=404, detail="No override to remove")
    db.commit()


@router.post("/reload")
async def reload_definitions(
    current_user: User = Depends(require_platform_admin()),
):
    """Reload all skill .md definitions from disk (admin only)."""
    registry = get_registry()
    registry.load_definitions()

    return {
        "success": True,
        "definitions_loaded": len(registry._definitions),
        "message": "All skill definitions reloaded from disk",
    }
