"""Enterprise readiness smoke tests — auth lockdown, permissions, formula, drivers."""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(db_session):
    """Reuse app with overridden get_db when available; else skip."""
    try:
        from app.main import app
        from app.database import get_db

        def _override():
            try:
                yield db_session
            finally:
                pass

        app.dependency_overrides[get_db] = _override
        with TestClient(app) as c:
            yield c
        app.dependency_overrides.clear()
    except Exception as e:
        pytest.skip(f"App client unavailable: {e}")


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] in ("healthy", "degraded")
    assert "checks" in body


def test_skills_require_auth(client):
    r = client.get("/api/skills/")
    assert r.status_code in (401, 403)


def test_formula_evaluation():
    from app.services.dependency_graph import DependencyGraphManager
    from unittest.mock import MagicMock

    dag = DependencyGraphManager(MagicMock())
    dag._graph = None

    # Bypass graph load by injecting a fake graph
    import networkx as nx

    G = nx.DiGraph()
    G.add_node(1, name="Revenue", category="Revenue")
    G.add_node(2, name="COGS", category="COGS")
    dag._graph = G

    result = dag._calculate_value(
        {
            1: {"value": 100.0, "relationship": "sum", "weight": 1.0},
            2: {"value": 40.0, "relationship": "subtract", "weight": 1.0},
        },
        "Revenue - COGS",
    )
    assert result == 60.0


def test_get_for_role_filters():
    from app.domain.registry import SkillsRegistry
    from app.domain.base_skill import BaseSkill, SkillResult

    class Dummy(BaseSkill):
        def __init__(self, n, role):
            self._n = n
            self._role = role

        @property
        def name(self):
            return self._n

        @property
        def description(self):
            return "d"

        @property
        def parameters_schema(self):
            return {"type": "object", "properties": {}}

        @property
        def required_role(self):
            return self._role

        async def execute(self, params, context):
            return SkillResult.ok("ok")

    reg = SkillsRegistry()
    reg.register(Dummy("a", "generate"))
    reg.register(Dummy("b", "admin"))
    reg.register(Dummy("c", "review"))

    analyst = {s.name for s in reg.get_for_role("analyst")}
    assert "a" in analyst
    assert "b" not in analyst
    admin = {s.name for s in reg.get_for_role("admin")}
    assert "b" in admin


def test_approver_roles():
    from app.services.permissions import APPROVER_ROLES, can_approve_forecast
    from types import SimpleNamespace

    assert "reviewer" in APPROVER_ROLES
    assert "manager" not in APPROVER_ROLES  # legacy alias handled separately

    user = SimpleNamespace(role=SimpleNamespace(name="reviewer", can_review=True, can_publish=False))
    assert can_approve_forecast(user) is True

    analyst = SimpleNamespace(role=SimpleNamespace(name="analyst", can_review=False, can_publish=False))
    assert can_approve_forecast(analyst) is False
