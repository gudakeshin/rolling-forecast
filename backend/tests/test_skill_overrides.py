"""Per-company skill-definition overrides layered over the global .md files."""

from __future__ import annotations

import os
import uuid

import pytest
from fastapi.testclient import TestClient
from passlib.context import CryptContext

from app.domain.registry import register_all_skills
from app.domain.skill_loader import SkillDefinition, apply_skill_override
from app.services.skill_overrides import (
    delete_skill_override,
    effective_skill_definitions,
    list_skill_overrides,
    set_skill_override,
)

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")

BASE = SkillDefinition(
    name="demo_skill",
    description="Global description.",
    required_role="generate",
    parameters=[{"name": "x", "type": "string"}],
    tags=["global"],
    instructions="## When to Use\nGlobal guidance.\n\n## Examples\n- \"global example\"",
    when_to_use="Global guidance.",
    examples=["global example"],
)


# --- apply_skill_override ---------------------------------------------------

def test_override_replaces_description_and_body():
    out = apply_skill_override(
        BASE,
        "---\ndescription: Company wording.\ntags: [acme]\n---\n"
        "## When to Use\nAcme guidance.\n\n## Examples\n- \"acme example\"\n",
    )
    assert out.description == "Company wording."
    assert out.tags == ["acme"]
    assert out.when_to_use == "Acme guidance."
    assert out.examples == ["acme example"]
    # Bound to code and permissions: never overridable.
    assert out.parameters == BASE.parameters
    assert out.required_role == "generate"


def test_frontmatter_only_override_keeps_base_body():
    out = apply_skill_override(BASE, "---\ndescription: Company wording.\n---\n")
    assert out.description == "Company wording."
    assert out.when_to_use == BASE.when_to_use
    assert out.instructions == BASE.instructions


@pytest.mark.parametrize(
    "content, message",
    [
        ("---\nrequired_role: input\n---\n", "can't set"),
        ("---\nparameters: []\n---\n", "can't set"),
        ("---\nname: other_skill\n---\n", "doesn't match"),
        ("---\ntags: acme\n---\n", "must be a list"),
        ("no frontmatter here", "frontmatter"),
        ("---\n- just\n- a list\n---\n", "mapping"),
    ],
)
def test_invalid_overrides_are_rejected(content, message):
    with pytest.raises(ValueError, match=message):
        apply_skill_override(BASE, content)


# --- service ------------------------------------------------------------------

@pytest.fixture
def registry():
    return register_all_skills()


def test_set_list_effective_and_delete(db_session, seed_business_unit, seed_users, registry):
    bu_id = seed_business_unit.id
    admin = seed_users["admin"]
    base_desc = registry.get_definition("generate_baseline").description

    defs, fp = effective_skill_definitions(db_session, business_unit_id=bu_id)
    assert fp == ""
    assert defs["generate_baseline"].description == base_desc

    set_skill_override(
        db_session, business_unit_id=bu_id, skill_name="generate_baseline",
        content="---\ndescription: Always state the fiscal year.\n---\n", actor=admin,
    )
    assert list(list_skill_overrides(db_session, business_unit_id=bu_id)) == ["generate_baseline"]
    defs, fp1 = effective_skill_definitions(db_session, business_unit_id=bu_id)
    assert defs["generate_baseline"].description == "Always state the fiscal year."
    assert fp1
    # The registry's own (global) definition is untouched.
    assert registry.get_definition("generate_baseline").description == base_desc

    set_skill_override(
        db_session, business_unit_id=bu_id, skill_name="generate_baseline",
        content="---\ndescription: Different wording.\n---\n", actor=admin,
    )
    _, fp2 = effective_skill_definitions(db_session, business_unit_id=bu_id)
    assert fp2 not in ("", fp1)

    assert delete_skill_override(
        db_session, business_unit_id=bu_id, skill_name="generate_baseline", actor=admin
    )
    assert effective_skill_definitions(db_session, business_unit_id=bu_id)[1] == ""
    assert not delete_skill_override(
        db_session, business_unit_id=bu_id, skill_name="generate_baseline", actor=admin
    )


def test_set_rejects_unknown_skill_and_oversized_content(db_session, seed_business_unit, seed_users, registry):
    with pytest.raises(LookupError):
        set_skill_override(
            db_session, business_unit_id=seed_business_unit.id, skill_name="no_such_skill",
            content="---\ndescription: x\n---\n", actor=seed_users["admin"],
        )
    with pytest.raises(ValueError, match="exceeds"):
        set_skill_override(
            db_session, business_unit_id=seed_business_unit.id, skill_name="generate_baseline",
            content="---\ndescription: x\n---\n" + "y" * 21000, actor=seed_users["admin"],
        )


def test_no_company_means_no_overrides(db_session, registry):
    defs, fp = effective_skill_definitions(db_session, business_unit_id=None)
    assert fp == ""
    assert defs.keys() == registry.definitions_snapshot().keys()


def test_agent_tools_use_the_given_definitions(db_session, seed_business_unit, seed_users, registry):
    set_skill_override(
        db_session, business_unit_id=seed_business_unit.id, skill_name="generate_baseline",
        content="---\ndescription: ACME-SPECIFIC WORDING.\n---\n", actor=seed_users["admin"],
    )
    defs, _ = effective_skill_definitions(db_session, business_unit_id=seed_business_unit.id)
    tools = {t.name: t for t in registry.as_langchain_tools(definitions=defs)}
    assert tools["generate_baseline"].description.startswith("ACME-SPECIFIC WORDING.")
    plain = {t.name: t for t in registry.as_langchain_tools()}
    assert not plain["generate_baseline"].description.startswith("ACME-SPECIFIC WORDING.")


# --- API ----------------------------------------------------------------------

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


@pytest.fixture
def companies():
    """A company admin and analyst in company A, a company admin in company B."""
    from app.database import SessionLocal
    from app.models.business_unit import BusinessUnit
    from app.models.company import Company
    from app.models.user import Role, User

    tag = uuid.uuid4().hex[:8]
    db = SessionLocal()
    try:
        roles = {r.name: r for r in db.query(Role).all()}
        bus = {}
        for side in ("A", "B"):
            company = Company(name=f"SkillOvCo-{side}-{tag}")
            db.add(company)
            db.flush()
            bu = BusinessUnit(name=f"SkillOvBU-{side}-{tag}", company_id=company.id)
            db.add(bu)
            db.flush()
            bus[side] = bu.id
        out = {"bu_a": bus["A"], "bu_b": bus["B"]}
        for key, side, role in (("admin_a", "A", "admin"), ("analyst_a", "A", "analyst"), ("admin_b", "B", "admin")):
            username = f"so_{key}_{tag}"
            db.add(User(
                email=f"{username}@test.local", username=username,
                hashed_password=_pwd.hash(username), full_name=key,
                business_unit_id=bus[side], role_id=roles[role].id,
            ))
            out[key] = username
        db.commit()
        return out
    finally:
        db.close()


def _h(client, username):
    r = client.post("/api/auth/login", json={"username": username, "password": username})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


OVERRIDE = "---\ndescription: Company A wording.\n---\n"


def test_company_admin_manages_own_override(client, companies):
    h = _h(client, companies["admin_a"])
    r = client.put("/api/skills/generate_baseline/override", json={"content": OVERRIDE}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["effective"]["description"] == "Company A wording."

    r = client.get("/api/skills/generate_baseline/override", headers=h)
    assert r.json()["content"] == OVERRIDE

    # The analyst in the same company sees the effective wording too.
    r = client.get("/api/skills/generate_baseline/override", headers=_h(client, companies["analyst_a"]))
    assert r.json()["effective"]["description"] == "Company A wording."

    assert client.delete("/api/skills/generate_baseline/override", headers=h).status_code == 204
    assert client.get("/api/skills/generate_baseline/override", headers=h).json()["content"] is None
    assert client.delete("/api/skills/generate_baseline/override", headers=h).status_code == 404


def test_override_is_invisible_to_and_unwritable_by_other_companies(client, companies):
    h_a = _h(client, companies["admin_a"])
    h_b = _h(client, companies["admin_b"])
    client.put("/api/skills/generate_baseline/override", json={"content": OVERRIDE}, headers=h_a)

    r = client.get("/api/skills/generate_baseline/override", headers=h_b)
    assert r.json()["content"] is None
    assert r.json()["effective"]["description"] != "Company A wording."

    r = client.put(
        "/api/skills/generate_baseline/override",
        json={"content": OVERRIDE, "business_unit_id": companies["bu_a"]},
        headers=h_b,
    )
    assert r.status_code == 404
    r = client.get(
        f"/api/skills/generate_baseline/override?business_unit_id={companies['bu_a']}", headers=h_b
    )
    assert r.status_code == 404


def test_analyst_cannot_write_overrides(client, companies):
    r = client.put(
        "/api/skills/generate_baseline/override",
        json={"content": OVERRIDE},
        headers=_h(client, companies["analyst_a"]),
    )
    assert r.status_code == 403


def test_invalid_override_is_400_and_unknown_skill_404(client, companies):
    h = _h(client, companies["admin_a"])
    r = client.put(
        "/api/skills/generate_baseline/override",
        json={"content": "---\nrequired_role: input\n---\n"},
        headers=h,
    )
    assert r.status_code == 400
    r = client.put("/api/skills/no_such_skill/override", json={"content": OVERRIDE}, headers=h)
    assert r.status_code == 404


def test_company_admin_cannot_edit_another_companys_analysis_profile(client, companies):
    r = client.put(
        f"/api/admin/business-units/{companies['bu_a']}/analysis-profile",
        json={"patch": {"materiality_share": 0.5}},
        headers=_h(client, companies["admin_b"]),
    )
    assert r.status_code == 404
    r = client.put(
        f"/api/admin/business-units/{companies['bu_a']}/analysis-profile",
        json={"patch": {"materiality_share": 0.5}},
        headers=_h(client, companies["admin_a"]),
    )
    assert r.status_code == 200, r.text


def test_agent_graph_is_rebuilt_when_company_overrides_change(monkeypatch):
    """The override fingerprint is part of the compiled-graph cache key."""
    from app.orchestration import master_agent as ma
    from app.services import skill_overrides

    ma.invalidate_agent_cache()
    built: list[object] = []

    def fake_create_react_agent(**kwargs):
        built.append(object())
        return built[-1]

    monkeypatch.setattr(ma, "create_react_agent", fake_create_react_agent)
    monkeypatch.setattr(ma, "ChatAnthropic", lambda **kwargs: object())

    class FakeRegistry:
        definitions_generation = 0
        seen: list = []

        def as_langchain_tools(self, ctx=None, definitions=None):
            self.seen.append(definitions)
            return []

    registry = FakeRegistry()
    monkeypatch.setattr(ma, "get_registry", lambda: registry)

    fingerprint = {"value": ""}
    monkeypatch.setattr(
        skill_overrides,
        "effective_skill_definitions",
        lambda db, business_unit_id: ({"x": "defn"}, fingerprint["value"]),
    )

    class FakeCM:
        user_id = "u1"
        user_role = "analyst"
        conversation_id = "c1"
        user = None
        _working_memory: dict = {}

    def agent():
        return ma.MasterAgent(context_manager=FakeCM(), db=None)._get_agent()  # type: ignore[arg-type]

    first = agent()
    assert agent() is first  # no overrides: cached
    assert registry.seen[-1] is None  # global definitions used

    fingerprint["value"] = "abc"
    overridden = agent()
    assert overridden is not first
    assert registry.seen[-1] == {"x": "defn"}
    assert agent() is overridden

    fingerprint["value"] = "def"
    assert agent() is not overridden
    ma.invalidate_agent_cache()
