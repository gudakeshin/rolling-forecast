"""Fail-closed session-level tenant scoping (app/services/tenant_scope.py).

Uses the `client`/TestClient(app) fixture pattern (not `db_session`) --
db_session is bound to a separate in-memory sessionmaker the tenant-scope
listener is never registered against (see app/services/tenant_scope.py's
own docstring), so only the real app stack (SessionLocal) can exercise it.

Each test queries via a RAW, deliberately unscoped `db.query(Model).all()`
-- exactly the shape of a forgotten per-endpoint check -- to prove the
listener itself is what's restricting results, independent of any
individual endpoint's own (already-audited) filtering.
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient
from passlib.context import CryptContext

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")


@pytest.fixture
def client():
    os.environ["SEED_DEMO_USERS"] = "true"
    os.environ["APP_ENV"] = "test"
    from app.main import app
    from app.rate_limit import limiter

    try:
        limiter.reset()
    except Exception:
        pass

    with TestClient(app) as c:
        yield c


def _login(client: TestClient, username: str, password: str) -> str:
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _make_scoped_fixtures(suffix: str):
    """Two companies, one user each, one LineItem each -- created directly
    via SessionLocal (the real app DB, a persistent file across test runs --
    see tests/test_business_unit_scoping.py's parallel-sessions memory note),
    not the isolated db_session fixture. `suffix` is namespaced with a fresh
    uuid so repeated runs never collide with a prior run's leftover rows."""
    import uuid

    from app.database import SessionLocal
    from app.models.business_unit import BusinessUnit
    from app.models.company import Company
    from app.models.line_item import LineItem
    from app.models.user import Role, User

    suffix = f"{suffix}-{uuid.uuid4().hex[:8]}"
    db = SessionLocal()
    try:
        role = db.query(Role).filter(Role.name == "analyst").first()
        assert role is not None, "expected demo seed roles to exist"

        company_a = Company(name=f"TenantScopeCo-A-{suffix}")
        company_b = Company(name=f"TenantScopeCo-B-{suffix}")
        db.add_all([company_a, company_b])
        db.flush()
        bu_a = BusinessUnit(name=f"TenantScopeBU-A-{suffix}", company_id=company_a.id)
        bu_b = BusinessUnit(name=f"TenantScopeBU-B-{suffix}", company_id=company_b.id)
        db.add_all([bu_a, bu_b])
        db.flush()

        user_a = User(
            email=f"tsa-{suffix}@test.local",
            username=f"tsa_{suffix}",
            hashed_password=_pwd.hash(f"tsa_{suffix}"),
            full_name="Tenant Scope A",
            business_unit_id=bu_a.id,
            role_id=role.id,
        )
        db.add(user_a)

        li_a = LineItem(
            account_code=f"TS-{suffix}-A", name="Scoped A", category="Revenue",
            business_unit_id=bu_a.id,
        )
        li_b = LineItem(
            account_code=f"TS-{suffix}-B", name="Scoped B", category="Revenue",
            business_unit_id=bu_b.id,
        )
        db.add_all([li_a, li_b])
        db.commit()
        return {
            "username": user_a.username,
            "password": f"tsa_{suffix}",
            "bu_a_id": bu_a.id,
            "bu_b_id": bu_b.id,
            "li_a_id": li_a.id,
            "li_b_id": li_b.id,
        }
    finally:
        db.close()


def test_unscoped_query_still_hides_another_companys_row(client):
    """The core guarantee: a raw, unfiltered query for a scoped model never
    returns another company's row once a request has resolved a
    single-company user -- even though this query itself applies no
    business_unit_id filter at all."""
    from app.database import SessionLocal
    from app.models.line_item import LineItem
    from app.services.permissions import can_access_business_unit
    from app.services.tenant_scope import push_tenant_scope, reset_tenant_scope, scope_for_user
    from app.models.user import User

    fx = _make_scoped_fixtures("core")
    _login(client, fx["username"], fx["password"])  # exercises get_current_user's push, sanity-checks login works

    db = SessionLocal()
    try:
        user_a = db.query(User).filter(User.username == fx["username"]).first()
        assert not can_access_business_unit(user_a, fx["bu_b_id"])  # sanity: genuinely a different company

        token = push_tenant_scope(scope_for_user(user_a))
        try:
            rows = db.query(LineItem).filter(LineItem.id.in_([fx["li_a_id"], fx["li_b_id"]])).all()
        finally:
            reset_tenant_scope(token)

        ids = {r.id for r in rows}
        assert fx["li_a_id"] in ids
        assert fx["li_b_id"] not in ids
    finally:
        db.close()


def test_no_scope_pushed_denies_everything(client):
    """Fail-closed default: if nothing ever pushed a scope for this context
    (e.g. a hypothetical background job that forgot to), a scoped-model
    query returns nothing rather than everything."""
    from app.database import SessionLocal
    from app.models.line_item import LineItem

    fx = _make_scoped_fixtures("noscope")
    db = SessionLocal()
    try:
        # Deliberately no push_tenant_scope call in this test's context.
        rows = db.query(LineItem).filter(LineItem.id.in_([fx["li_a_id"], fx["li_b_id"]])).all()
        assert rows == []
    finally:
        db.close()


def test_unrestricted_scope_sees_everything(client):
    from app.database import SessionLocal
    from app.models.line_item import LineItem
    from app.services.tenant_scope import UNRESTRICTED, push_tenant_scope, reset_tenant_scope

    fx = _make_scoped_fixtures("admin")
    db = SessionLocal()
    try:
        token = push_tenant_scope(UNRESTRICTED)
        try:
            rows = db.query(LineItem).filter(LineItem.id.in_([fx["li_a_id"], fx["li_b_id"]])).all()
        finally:
            reset_tenant_scope(token)
        ids = {r.id for r in rows}
        assert {fx["li_a_id"], fx["li_b_id"]} == ids
    finally:
        db.close()


def test_model_preset_global_rows_stay_visible_under_scoping(client):
    """ModelPreset's NULL business_unit_id (the global catalog) must not be
    hidden by the loader criteria for a single-company scope."""
    from app.database import SessionLocal
    from app.models.model_preset import ModelPreset
    from app.models.user import User
    from app.services.tenant_scope import push_tenant_scope, reset_tenant_scope, scope_for_user

    fx = _make_scoped_fixtures("preset")
    db = SessionLocal()
    try:
        global_preset = ModelPreset(name="TenantScopeGlobalPreset", model_type="auto")
        other_company_preset = ModelPreset(
            name="TenantScopeOtherPreset", model_type="auto", business_unit_id=fx["bu_b_id"]
        )
        db.add_all([global_preset, other_company_preset])
        db.commit()

        user_a = db.query(User).filter(User.username == fx["username"]).first()
        token = push_tenant_scope(scope_for_user(user_a))
        try:
            visible = {
                p.id
                for p in db.query(ModelPreset)
                .filter(ModelPreset.id.in_([global_preset.id, other_company_preset.id]))
                .all()
            }
        finally:
            reset_tenant_scope(token)

        assert global_preset.id in visible
        assert other_company_preset.id not in visible
    finally:
        db.close()


def _make_child_fixtures(fx: dict) -> dict:
    """One ForecastVersion -> ForecastLineResult -> ModelMetadata chain and
    one Driver -> DriverValue pair per company, on top of _make_scoped_fixtures."""
    import uuid

    from app.database import SessionLocal
    from app.models.driver import Driver, DriverValue
    from app.models.forecast import ForecastLineResult, ForecastVersion, ModelMetadata

    tag = uuid.uuid4().hex[:8]
    db = SessionLocal()
    try:
        out: dict = {}
        for side in ("a", "b"):
            bu_id, li_id = fx[f"bu_{side}_id"], fx[f"li_{side}_id"]
            version = ForecastVersion(name=f"TenantScopeFV-{side}-{tag}", business_unit_id=bu_id)
            driver = Driver(key=f"ts_{side}_{tag}", name="Scoped driver", business_unit_id=bu_id)
            db.add_all([version, driver])
            db.flush()
            result = ForecastLineResult(
                version_id=version.id, line_item_id=li_id, period="2026-01", p50=1.0
            )
            value = DriverValue(driver_id=driver.id, period="2026-01", value=1.0)
            db.add_all([result, value])
            db.flush()
            metadata = ModelMetadata(line_result_id=result.id, model_type="linear")
            db.add(metadata)
            db.flush()
            out[f"flr_{side}"] = result.id
            out[f"meta_{side}"] = metadata.id
            out[f"dv_{side}"] = value.id
        db.commit()
        return out
    finally:
        db.close()


def _visible_child_ids(scope, cx: dict) -> dict[str, set]:
    """Raw by-id child queries with no join back to any parent -- the exact
    shape a parent-only loader criterion would miss."""
    from app.database import SessionLocal
    from app.models.driver import DriverValue
    from app.models.forecast import ForecastLineResult, ModelMetadata
    from app.services.tenant_scope import push_tenant_scope, reset_tenant_scope

    db = SessionLocal()
    token = push_tenant_scope(scope) if scope is not None else None
    try:
        return {
            "flr": {
                r.id
                for r in db.query(ForecastLineResult)
                .filter(ForecastLineResult.id.in_([cx["flr_a"], cx["flr_b"]]))
                .all()
            },
            "meta": {
                r.id
                for r in db.query(ModelMetadata)
                .filter(ModelMetadata.id.in_([cx["meta_a"], cx["meta_b"]]))
                .all()
            },
            "dv": {
                r.id
                for r in db.query(DriverValue)
                .filter(DriverValue.id.in_([cx["dv_a"], cx["dv_b"]]))
                .all()
            },
        }
    finally:
        if token is not None:
            reset_tenant_scope(token)
        db.close()


def test_child_tables_hide_another_companys_rows(client):
    """Child and grandchild rows (no business_unit_id of their own) queried
    directly by id are still restricted to the scoped company via their
    owning parent."""
    from app.database import SessionLocal
    from app.models.user import User
    from app.services.tenant_scope import scope_for_user

    fx = _make_scoped_fixtures("child")
    cx = _make_child_fixtures(fx)
    db = SessionLocal()
    try:
        user_a = db.query(User).filter(User.username == fx["username"]).first()
        scope = scope_for_user(user_a)
    finally:
        db.close()

    seen = _visible_child_ids(scope, cx)
    assert seen["flr"] == {cx["flr_a"]}
    assert seen["meta"] == {cx["meta_a"]}
    assert seen["dv"] == {cx["dv_a"]}


def test_child_tables_deny_when_no_scope_pushed(client):
    fx = _make_scoped_fixtures("child-noscope")
    cx = _make_child_fixtures(fx)
    seen = _visible_child_ids(None, cx)
    assert seen == {"flr": set(), "meta": set(), "dv": set()}


def test_child_tables_unrestricted_sees_both_companies(client):
    from app.services.tenant_scope import UNRESTRICTED

    fx = _make_scoped_fixtures("child-admin")
    cx = _make_child_fixtures(fx)
    seen = _visible_child_ids(UNRESTRICTED, cx)
    assert seen["flr"] == {cx["flr_a"], cx["flr_b"]}
    assert seen["meta"] == {cx["meta_a"], cx["meta_b"]}
    assert seen["dv"] == {cx["dv_a"], cx["dv_b"]}


def test_child_filtered_by_other_companys_parent_fk_returns_nothing(client):
    """Knowing another company's version id and filtering the child table on
    that FK directly -- no join to ForecastVersion -- still returns nothing."""
    from app.database import SessionLocal
    from app.models.forecast import ForecastLineResult
    from app.models.user import User
    from app.services.tenant_scope import (
        UNRESTRICTED,
        push_tenant_scope,
        reset_tenant_scope,
        scope_for_user,
    )

    fx = _make_scoped_fixtures("child-fk")
    cx = _make_child_fixtures(fx)
    db = SessionLocal()
    try:
        token = push_tenant_scope(UNRESTRICTED)
        try:
            b_version_id = db.get(ForecastLineResult, cx["flr_b"]).version_id
            user_a = db.query(User).filter(User.username == fx["username"]).first()
        finally:
            reset_tenant_scope(token)
        db.expire_all()

        token = push_tenant_scope(scope_for_user(user_a))
        try:
            rows = (
                db.query(ForecastLineResult)
                .filter(ForecastLineResult.version_id == b_version_id)
                .all()
            )
        finally:
            reset_tenant_scope(token)
        assert rows == []
    finally:
        db.close()
