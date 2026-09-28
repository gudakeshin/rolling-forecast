"""Fail-closed, session-level tenant scoping -- a safety net behind the
per-endpoint checks in app/services/permissions.py, not a replacement for
them.

Every confirmed cross-company leak reachable today has been closed by hand
(see the enterprise-readiness Phase 2 commits). This module protects
against a *future* forgotten check: it auto-injects a
``business_unit_id`` filter into every SELECT against a fixed list of
tenant-scoped models, for any session created from the given
``session_factory`` (in practice, ``app.database.SessionLocal`` -- unit
tests use a *separate* sessionmaker bound to an in-memory DB and are
untouched by this).

Coverage is intentionally narrow: only models with their OWN direct
``business_unit_id`` column. SQLAlchemy's ``with_loader_criteria`` only
auto-applies when the registered entity itself appears in a query's FROM
clause -- it does nothing for a child table queried by its own id with no
join back to its parent (e.g. ``ForecastLineResult`` by ``id``). Those are a
distinct, correlated-subquery problem, not attempted here.

Default is DENY, not allow: if no scope was ever pushed for the current
context (e.g. a background job that forgot to), every scoped-model query
returns nothing rather than everything. That's the only default that
actually fails closed -- see app/workers/arq_worker.py for the jobs that
must explicitly opt into UNRESTRICTED or a specific company's scope.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass

from sqlalchemy import event, false, or_
from sqlalchemy.orm import with_loader_criteria


@dataclass(frozen=True)
class TenantScope:
    unrestricted: bool
    business_unit_id: str | None = None


UNRESTRICTED = TenantScope(unrestricted=True)

_current: ContextVar["TenantScope | None"] = ContextVar("rf_tenant_scope", default=None)


def push_tenant_scope(scope: TenantScope) -> Token:
    return _current.set(scope)


def reset_tenant_scope(token: Token) -> None:
    _current.reset(token)


def scope_for_user(user) -> TenantScope:
    """Derive the scope a request/job should operate under from its actor.

    Mirrors app.services.permissions.can_access_business_unit's own rule:
    no business_unit_id at all (and not cross-BU) means "see nothing", not
    "see everything."
    """
    from app.services.permissions import can_view_all_bus

    if user is None or can_view_all_bus(user):
        return UNRESTRICTED
    return TenantScope(unrestricted=False, business_unit_id=user.business_unit_id)


_registered_factories: set[int] = set()


def register(session_factory) -> None:
    """Attach the do_orm_execute listener to `session_factory`. Call once,
    after app.models has been imported (see app/database.py:init_db) --
    idempotent per factory, since init_db() can run more than once (e.g.
    once per TestClient(app) lifespan cycle in tests)."""
    if id(session_factory) in _registered_factories:
        return
    _registered_factories.add(id(session_factory))

    from app.models.business_unit import BusinessUnit  # noqa: F401 -- ensures mappers configured
    from app.models.driver import Driver
    from app.models.driver_input import DriverFormConfig, DriverInput
    from app.models.forecast import ForecastVersion
    from app.models.actuals import ActualsDataset
    from app.models.budget import BudgetVersion
    from app.models.integration import IntegrationConnection
    from app.models.line_item import LineItem
    from app.models.model_preset import ModelPreset

    plain_scoped_models = (
        LineItem,
        ForecastVersion,
        ActualsDataset,
        Driver,
        BudgetVersion,
        DriverInput,
        DriverFormConfig,
        IntegrationConnection,
    )

    @event.listens_for(session_factory, "do_orm_execute")
    def _apply_tenant_scope(orm_execute_state):
        if not orm_execute_state.is_select:
            return
        scope = _current.get()
        if scope is not None and scope.unrestricted:
            return  # explicit opt-out (admin, or a system job that pushed UNRESTRICTED)

        # scope is None ("nobody ever pushed one for this context") is
        # deliberately NOT the same as unrestricted -- it must deny, not
        # allow, or an unscoped context (e.g. a background job that forgot
        # to push a scope) would see everything instead of nothing.
        for cls in plain_scoped_models:
            criteria = false() if scope is None else cls.business_unit_id == scope.business_unit_id
            orm_execute_state.statement = orm_execute_state.statement.options(
                with_loader_criteria(cls, criteria, include_aliases=True)
            )

        # ModelPreset: NULL business_unit_id is the global catalog, visible
        # to every company -- see app/services/model_presets.py. Still
        # deny-all (not "show only globals") when scope is None, matching
        # every other scoped model's fail-closed default.
        preset_criteria = (
            false()
            if scope is None
            else or_(
                ModelPreset.business_unit_id == scope.business_unit_id,
                ModelPreset.business_unit_id.is_(None),
            )
        )
        orm_execute_state.statement = orm_execute_state.statement.options(
            with_loader_criteria(ModelPreset, preset_criteria, include_aliases=True)
        )
