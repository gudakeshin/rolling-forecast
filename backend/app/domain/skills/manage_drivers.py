"""manage_drivers skill — CRUD / list / link causal drivers (Phase U2)."""

from __future__ import annotations

import logging
from typing import Any

from app.domain.base_skill import BaseSkill, SkillContext, SkillResult
from app.services.permissions import resolve_skill_user, user_has_permission

logger = logging.getLogger(__name__)


class ManageDriversSkill(BaseSkill):
    @property
    def name(self) -> str:
        return "manage_drivers"

    @property
    def description(self) -> str:
        return (
            "Manage causal driver series (not BU assumption forms). "
            "List drivers with freshness, create a driver, assert a link to a line item, "
            "or open the Driver Series panel. Use when the user asks about headcount, "
            "volume, price, macro drivers, or linking drivers to P&L lines."
        )

    @property
    def required_role(self) -> str:
        return "manage_drivers"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "create", "assert_link", "open_panel"],
                    "description": "Action to perform",
                },
                "key": {"type": "string", "description": "Driver key (create)"},
                "name": {"type": "string", "description": "Display name (create)"},
                "driver_type": {
                    "type": "string",
                    "description": "volume|price|headcount|macro|index|other",
                    "default": "other",
                },
                "unit": {"type": "string"},
                "business_unit": {"type": "string"},
                "driver_id": {"type": "integer"},
                "line_item_id": {"type": "integer"},
                "relation": {
                    "type": "string",
                    "description": "level|quantity|unit_price|elasticity",
                    "default": "level",
                },
                "lag": {"type": "integer", "default": 0},
                "coefficient": {"type": "number"},
                "include_freshness": {"type": "boolean", "default": True},
            },
            "required": ["action"],
        }

    async def execute(self, params: dict[str, Any], context: SkillContext) -> SkillResult:
        action = (params.get("action") or "").strip()
        user = resolve_skill_user(context)
        if user is None:
            return SkillResult.fail("Authenticated user required.")
        if not user_has_permission(user, "manage_drivers"):
            return SkillResult.fail("Permission manage_drivers required.")

        if action == "open_panel":
            return SkillResult.ok(
                message="Opening Driver Series panel.",
                panel_payload={"panel": "drivers", "params": {}, "title": "Driver Series"},
            )

        if action == "list":
            return await self._list(params, context)
        if action == "create":
            return await self._create(params, context, user)
        if action == "assert_link":
            return await self._assert_link(params, context, user)
        return SkillResult.fail(f"Unknown action: {action}")

    async def _list(self, params: dict, context: SkillContext) -> SkillResult:
        from app.models.driver import Driver
        from app.services.driver_ingest import driver_freshness
        from app.services.permissions import scoped_drivers

        user = resolve_skill_user(context)
        include_fresh = bool(params.get("include_freshness", True))
        rows = scoped_drivers(context.db, user).order_by(Driver.key).limit(50).all()
        out = []
        for d in rows:
            item = {
                "id": d.id,
                "key": d.key,
                "name": d.name,
                "driver_type": d.driver_type,
                "business_unit": d.business_unit,
            }
            if include_fresh:
                item["freshness"] = driver_freshness(context.db, d.id)
            out.append(item)

        stale = sum(1 for r in out if (r.get("freshness") or {}).get("stale"))
        lines = [
            f"**{len(out)} drivers** (showing up to 50)"
            + (f", {stale} stale" if include_fresh else ""),
        ]
        for r in out[:15]:
            fresh = ""
            if include_fresh and r.get("freshness"):
                f = r["freshness"]
                fresh = f" · last={f.get('last_period') or 'n/a'}" + (" stale" if f.get("stale") else "")
            lines.append(f"- `{r['key']}` — {r['name']} ({r['driver_type']}){fresh}")
        if len(out) > 15:
            lines.append(f"…and {len(out) - 15} more")

        return SkillResult.ok(
            message=f"Found {len(out)} drivers.",
            data={"drivers": out, "count": len(out)},
            content_blocks=[self._text_block("\n".join(lines))],
            panel_payload={"panel": "drivers", "params": {}, "title": "Driver Series"},
        )

    async def _create(self, params: dict, context: SkillContext, user) -> SkillResult:
        from app.services.driver_series import create_driver

        key = (params.get("key") or "").strip()
        name = (params.get("name") or "").strip()
        if not key or not name:
            return SkillResult.fail("key and name are required to create a driver.")
        try:
            driver = create_driver(
                context.db,
                key=key,
                name=name,
                driver_type=params.get("driver_type") or "other",
                unit=params.get("unit"),
                business_unit=params.get("business_unit") or getattr(user, "business_unit", None),
                source="manual",
                actor=user,
            )
            context.db.commit()
        except ValueError as e:
            return SkillResult.fail(str(e))
        return SkillResult.ok(
            message=f"Created driver `{driver.key}` (id={driver.id}).",
            data={"id": driver.id, "key": driver.key, "name": driver.name},
            panel_payload={"panel": "drivers", "params": {}, "title": "Driver Series"},
        )

    async def _assert_link(self, params: dict, context: SkillContext, user) -> SkillResult:
        from app.services.driver_series import assert_link

        driver_id = params.get("driver_id")
        line_item_id = params.get("line_item_id")
        if not driver_id or not line_item_id:
            return SkillResult.fail("driver_id and line_item_id are required.")
        try:
            link = assert_link(
                context.db,
                driver_id=int(driver_id),
                line_item_id=int(line_item_id),
                relation=params.get("relation") or "level",
                lag=int(params.get("lag") or 0),
                coefficient=params.get("coefficient"),
                status="candidate",
                actor=user,
            )
            context.db.commit()
        except ValueError as e:
            return SkillResult.fail(str(e))
        return SkillResult.ok(
            message=(
                f"Asserted link driver={link.driver_id} → line={link.line_item_id} "
                f"({link.relation}, status={link.status})."
            ),
            data={"link_id": link.id, "status": link.status},
            panel_payload={"panel": "drivers", "params": {}, "title": "Driver Series"},
        )
