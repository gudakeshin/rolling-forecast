"""Saved views — service layer and session-level tenant scoping."""

from __future__ import annotations

import os

import pytest
from passlib.context import CryptContext

from app.services.saved_views import create_view, delete_view, list_views

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")


class TestServiceLayer:
    def test_create_and_list(self, db_session, seed_users):
        view = create_view(
            db_session,
            name="Q3 Revenue",
            panel_type="forecast-table",
            panel_params={"category": "Revenue"},
            actor=seed_users["analyst"],
        )
        assert view.id
        assert view.business_unit_id == seed_users["analyst"].business_unit_id
        assert view.created_by == seed_users["analyst"].id

        views = list_views(db_session, actor=seed_users["analyst"])
        assert view.id in {v.id for v in views}

    def test_duplicate_name_same_user_rejected(self, db_session, seed_users):
        create_view(
            db_session, name="Dup", panel_type="dashboard", panel_params={},
            actor=seed_users["analyst"],
        )
        with pytest.raises(ValueError, match="already exists"):
            create_view(
                db_session, name="dup", panel_type="dashboard", panel_params={},
                actor=seed_users["analyst"],
            )

    def test_same_name_different_users_allowed(self, db_session, seed_users):
        v1 = create_view(
            db_session, name="Shared Name", panel_type="dashboard", panel_params={},
            actor=seed_users["analyst"],
        )
        v2 = create_view(
            db_session, name="Shared Name", panel_type="dashboard", panel_params={},
            actor=seed_users["admin"],
        )
        assert v1.id != v2.id

    def test_list_is_personal_not_shared(self, db_session, seed_users):
        create_view(
            db_session, name="Analyst Only", panel_type="dashboard", panel_params={},
            actor=seed_users["analyst"],
        )
        admin_views = list_views(db_session, actor=seed_users["admin"])
        assert "Analyst Only" not in {v.name for v in admin_views}

    def test_delete_by_owner(self, db_session, seed_users):
        view = create_view(
            db_session, name="ToDelete", panel_type="dashboard", panel_params={},
            actor=seed_users["analyst"],
        )
        delete_view(db_session, view.id, actor=seed_users["analyst"])
        remaining = list_views(db_session, actor=seed_users["analyst"])
        assert view.id not in {v.id for v in remaining}

    def test_delete_by_non_owner_rejected(self, db_session, seed_users):
        view = create_view(
            db_session, name="NotYours", panel_type="dashboard", panel_params={},
            actor=seed_users["analyst"],
        )
        with pytest.raises(ValueError, match="not found"):
            delete_view(db_session, view.id, actor=seed_users["admin"])

    def test_delete_nonexistent_rejected(self, db_session, seed_users):
        with pytest.raises(ValueError, match="not found"):
            delete_view(db_session, "no-such-id", actor=seed_users["analyst"])

    def test_create_without_business_unit_rejected(self, db_session, seed_users, seed_roles):
        from app.models.user import User

        no_bu_user = User(
            email="no-bu@test.local",
            username="no_bu_user",
            hashed_password=_pwd.hash("no_bu_user"),
            full_name="No BU",
            business_unit_id=None,
            role_id=seed_roles["analyst"].id,
        )
        db_session.add(no_bu_user)
        db_session.commit()

        with pytest.raises(ValueError, match="company"):
            create_view(
                db_session, name="Orphan", panel_type="dashboard", panel_params={},
                actor=no_bu_user,
            )

    def test_empty_name_rejected(self, db_session, seed_users):
        with pytest.raises(ValueError, match="required"):
            create_view(
                db_session, name="   ", panel_type="dashboard", panel_params={},
                actor=seed_users["analyst"],
            )


class TestTenantScope:
    """Fail-closed session-level scoping (app/services/tenant_scope.py) also
    covers SavedView -- mirrors test_tenant_scope.py's own pattern of
    proving the listener itself, via a deliberately unscoped raw query,
    rather than any individual endpoint's filtering."""

    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient

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

    def _login(self, client, username, password):
        r = client.post("/api/auth/login", json={"username": username, "password": password})
        assert r.status_code == 200, r.text
        return r.json()["access_token"]

    def _make_scoped_fixtures(self, suffix: str):
        import uuid

        from app.database import SessionLocal
        from app.models.business_unit import BusinessUnit
        from app.models.company import Company
        from app.models.saved_view import SavedView
        from app.models.user import Role, User

        suffix = f"{suffix}-{uuid.uuid4().hex[:8]}"
        db = SessionLocal()
        try:
            role = db.query(Role).filter(Role.name == "analyst").first()
            assert role is not None, "expected demo seed roles to exist"

            company_a = Company(name=f"SavedViewCo-A-{suffix}")
            company_b = Company(name=f"SavedViewCo-B-{suffix}")
            db.add_all([company_a, company_b])
            db.flush()
            bu_a = BusinessUnit(name=f"SavedViewBU-A-{suffix}", company_id=company_a.id)
            bu_b = BusinessUnit(name=f"SavedViewBU-B-{suffix}", company_id=company_b.id)
            db.add_all([bu_a, bu_b])
            db.flush()

            user_a = User(
                email=f"sva-{suffix}@test.local",
                username=f"sva_{suffix}",
                hashed_password=_pwd.hash(f"sva_{suffix}"),
                full_name="Saved View A",
                business_unit_id=bu_a.id,
                role_id=role.id,
            )
            db.add(user_a)
            db.flush()

            view_a = SavedView(
                name="View A", panel_type="dashboard", panel_params={},
                business_unit_id=bu_a.id, created_by=user_a.id,
            )
            view_b = SavedView(
                name="View B", panel_type="dashboard", panel_params={},
                business_unit_id=bu_b.id, created_by=None,
            )
            db.add_all([view_a, view_b])
            db.commit()
            return {
                "username": user_a.username,
                "password": f"sva_{suffix}",
                "view_a_id": view_a.id,
                "view_b_id": view_b.id,
            }
        finally:
            db.close()

    def test_unscoped_query_hides_another_companys_view(self, client):
        from app.database import SessionLocal
        from app.models.saved_view import SavedView
        from app.models.user import User
        from app.services.tenant_scope import push_tenant_scope, reset_tenant_scope, scope_for_user

        fx = self._make_scoped_fixtures("core")
        self._login(client, fx["username"], fx["password"])

        db = SessionLocal()
        try:
            user_a = db.query(User).filter(User.username == fx["username"]).first()
            token = push_tenant_scope(scope_for_user(user_a))
            try:
                rows = (
                    db.query(SavedView)
                    .filter(SavedView.id.in_([fx["view_a_id"], fx["view_b_id"]]))
                    .all()
                )
            finally:
                reset_tenant_scope(token)

            ids = {r.id for r in rows}
            assert fx["view_a_id"] in ids
            assert fx["view_b_id"] not in ids
        finally:
            db.close()

    def test_no_scope_pushed_denies_everything(self, client):
        from app.database import SessionLocal
        from app.models.saved_view import SavedView

        fx = self._make_scoped_fixtures("noscope")
        db = SessionLocal()
        try:
            rows = (
                db.query(SavedView)
                .filter(SavedView.id.in_([fx["view_a_id"], fx["view_b_id"]]))
                .all()
            )
            assert rows == []
        finally:
            db.close()
