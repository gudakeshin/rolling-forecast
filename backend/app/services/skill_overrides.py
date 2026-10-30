"""Per-company overrides of skill definitions, layered over the global .md files.

Stored as `BusinessUnitSetting` rows keyed ``skill.<name>`` (same additive
pattern as app.services.analysis_profile), holding an override .md that
`app.domain.skill_loader.apply_skill_override` merges onto the global
definition. Only LLM-facing text is overridable; see that function.
"""

from __future__ import annotations

import hashlib
from typing import Any

from sqlalchemy.orm import Session

from app.domain.registry import get_registry
from app.domain.skill_loader import SkillDefinition, apply_skill_override
from app.models.tenant_setting import BusinessUnitSetting
from app.services.audit import record_audit
from app.services.tenant_settings import set_tenant_setting

_KEY_PREFIX = "skill."
MAX_OVERRIDE_BYTES = 20 * 1024


def _rows(db: Session, business_unit_id: str):
    return db.query(BusinessUnitSetting).filter(
        BusinessUnitSetting.business_unit_id == business_unit_id,
        BusinessUnitSetting.key.startswith(_KEY_PREFIX, autoescape=True),
    )


def list_skill_overrides(db: Session, *, business_unit_id: str | None) -> dict[str, str]:
    """skill name -> override .md for one company (empty for no company)."""
    if not business_unit_id:
        return {}
    return {
        row.key[len(_KEY_PREFIX):]: row.value
        for row in _rows(db, business_unit_id).all()
        if row.value
    }


def get_skill_override(db: Session, *, business_unit_id: str, skill_name: str) -> str | None:
    row = _rows(db, business_unit_id).filter(
        BusinessUnitSetting.key == f"{_KEY_PREFIX}{skill_name}"
    ).first()
    return row.value if row is not None and row.value else None


def _base_definition(skill_name: str) -> SkillDefinition:
    defn = get_registry().get_definition(skill_name)
    if defn is None:
        raise LookupError(f"Skill '{skill_name}' has no definition to override")
    return defn


def set_skill_override(
    db: Session, *, business_unit_id: str, skill_name: str, content: str, actor: Any
) -> SkillDefinition:
    """Validate and store a company's override; returns the effective definition.
    Raises LookupError for an unknown skill, ValueError for invalid content."""
    base = _base_definition(skill_name)
    if len(content.encode("utf-8")) > MAX_OVERRIDE_BYTES:
        raise ValueError(f"Override exceeds {MAX_OVERRIDE_BYTES} bytes")
    effective = apply_skill_override(base, content)
    set_tenant_setting(
        db, f"{_KEY_PREFIX}{skill_name}", content, business_unit_id=business_unit_id
    )
    record_audit(
        db,
        action="skills.override.update",
        entity_type="skill",
        entity_id=skill_name,
        actor_id=getattr(actor, "id", None),
        actor_username=getattr(actor, "username", None),
        details={"business_unit_id": business_unit_id},
    )
    return effective


def delete_skill_override(
    db: Session, *, business_unit_id: str, skill_name: str, actor: Any
) -> bool:
    """Remove a company's override; True if one existed."""
    row = _rows(db, business_unit_id).filter(
        BusinessUnitSetting.key == f"{_KEY_PREFIX}{skill_name}"
    ).first()
    if row is None:
        return False
    db.delete(row)
    record_audit(
        db,
        action="skills.override.delete",
        entity_type="skill",
        entity_id=skill_name,
        actor_id=getattr(actor, "id", None),
        actor_username=getattr(actor, "username", None),
        details={"business_unit_id": business_unit_id},
    )
    return True


def effective_skill_definitions(
    db: Session, *, business_unit_id: str | None
) -> tuple[dict[str, SkillDefinition], str]:
    """(skill name -> definition with this company's overrides applied,
    fingerprint of those overrides -- "" when there are none).

    The fingerprint changes whenever an override's content does, so it can
    key a cache of anything built from the definitions. An override that no
    longer applies (its skill's base definition was removed, or it fails
    validation after a base edit) is skipped and the base is used."""
    registry = get_registry()
    definitions = registry.definitions_snapshot()
    overrides = list_skill_overrides(db, business_unit_id=business_unit_id)
    applied: list[tuple[str, str]] = []
    for name, content in sorted(overrides.items()):
        base = definitions.get(name)
        if base is None:
            continue
        try:
            definitions[name] = apply_skill_override(base, content)
        except ValueError:
            continue
        applied.append((name, content))
    if not applied:
        return definitions, ""
    digest = hashlib.sha256()
    for name, content in applied:
        digest.update(name.encode())
        digest.update(b"\0")
        digest.update(content.encode())
        digest.update(b"\0")
    return definitions, digest.hexdigest()[:16]
