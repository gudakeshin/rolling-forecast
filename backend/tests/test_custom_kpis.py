"""Custom KPI formulas: parsing, resolution, evaluation, API and chat skill."""

from __future__ import annotations

import os
import uuid

import pytest
from fastapi.testclient import TestClient
from passlib.context import CryptContext

from app.domain.kpi_formula import (
    FormulaError,
    Ref,
    evaluate,
    parse_formula,
    references,
)

# --- parser / evaluator -------------------------------------------------------


@pytest.mark.parametrize(
    "formula, expected",
    [
        ("1 + 2 * 3", 7.0),
        ("(1 + 2) * 3", 9.0),
        ("-2 * -3", 6.0),
        ("10 / 4 - .5", 2.0),
        ("2e2 / 100", 2.0),
        ("--3", 3.0),
    ],
)
def test_arithmetic_precedence(formula, expected):
    assert evaluate(parse_formula(formula), {}) == expected


def test_references_parse_with_kinds_and_dedupe():
    node = parse_formula("[Revenue] - [item:4000-100] + [ category : COGS ] + [Revenue]")
    assert references(node) == [
        Ref("any", "Revenue"),
        Ref("item", "4000-100"),
        Ref("category", "COGS"),
    ]


def test_missing_input_and_division_by_zero_give_no_value():
    node = parse_formula("[A] / [B]")
    a, b = references(node)
    assert evaluate(node, {a: 1.0, b: 0.0}) is None
    assert evaluate(node, {a: 1.0}) is None
    assert evaluate(node, {a: 1.0, b: 4.0}) == 0.25


@pytest.mark.parametrize(
    "formula, message",
    [
        ("", "empty"),
        ("Revenue / 2", "brackets"),
        ("[Revenue", "closing ']'"),
        ("([A] + 1", "closing '\\)'"),
        ("[A] +", "ends unexpectedly"),
        ("[]", "Empty reference"),
        ("[item:]", "Empty reference"),
        ("[A] [B]", "Unexpected"),
        ("__import__('os')", "Unexpected"),
        ("1" + "+1" * 300, "exceeds"),
        ("(" * 60 + "1" + ")" * 60, "nested"),
        ("+".join(f"[r{i}]" for i in range(51)), "more than"),
    ],
)
def test_invalid_formulas(formula, message):
    with pytest.raises(FormulaError, match=message):
        parse_formula(formula)


# --- resolution and evaluation ---------------------------------------------------


@pytest.fixture
def company(db_session):
    """A company with Revenue / COGS lines, a calculated Gross Margin line, and a
    version with two periods (one Revenue period overridden)."""
    from app.models.business_unit import BusinessUnit
    from app.models.forecast import ForecastLineResult, ForecastVersion
    from app.models.line_item import LineItem

    bu = BusinessUnit(name=f"KpiCo-{uuid.uuid4().hex[:6]}")
    db_session.add(bu)
    db_session.flush()
    rev = LineItem(account_code="4000", name="Product Revenue", category="Revenue", business_unit_id=bu.id)
    svc = LineItem(account_code="4100", name="Service Revenue", category="Revenue", business_unit_id=bu.id)
    cogs = LineItem(account_code="5000", name="Materials", category="COGS", business_unit_id=bu.id)
    gm = LineItem(
        account_code="6000", name="Gross Margin", category="Revenue",
        business_unit_id=bu.id, is_calculated=True,
    )
    db_session.add_all([rev, svc, cogs, gm])
    db_session.flush()
    version = ForecastVersion(name="KPI test version", business_unit_id=bu.id)
    db_session.add(version)
    db_session.flush()

    def row(li, period, p50, override=None):
        return ForecastLineResult(
            version_id=version.id, line_item_id=li.id, period=period, p50=p50,
            is_overridden=override is not None, override_value=override,
        )

    db_session.add_all([
        row(rev, "2026-01", 100.0), row(rev, "2026-02", 100.0, override=150.0),
        row(svc, "2026-01", 50.0), row(svc, "2026-02", 50.0),
        row(cogs, "2026-01", 60.0), row(cogs, "2026-02", 80.0),
        row(gm, "2026-01", 90.0), row(gm, "2026-02", 120.0),
    ])
    db_session.commit()
    return {"bu": bu, "version": version, "rev": rev, "gm": gm}


def _kpi(db, bu, name, formula, fmt="number"):
    from app.services.kpis import create_kpi

    return create_kpi(
        db, business_unit_id=bu.id, name=name, formula=formula, display_format=fmt, actor=None,
    )


def test_resolution_rules(db_session, company):
    from app.services.kpis import compile_kpi_formula

    bu_id = company["bu"].id
    _, resolved = compile_kpi_formula(
        db_session, business_unit_id=bu_id,
        formula="[Product Revenue] + [4100] + [COGS] + [item:Gross Margin]",
    )
    kinds = {str(ref): res[0] for ref, res in resolved.items()}
    assert kinds == {
        "[Product Revenue]": "item",
        "[4100]": "item",
        "[COGS]": "category",
        "[item:Gross Margin]": "item",
    }
    with pytest.raises(FormulaError, match="not a line item or category"):
        compile_kpi_formula(db_session, business_unit_id=bu_id, formula="[Nope]")
    with pytest.raises(FormulaError, match="not a category"):
        compile_kpi_formula(db_session, business_unit_id=bu_id, formula="[category:Materials]")


def test_bare_name_matching_item_and_category_is_ambiguous(db_session, company):
    from app.models.line_item import LineItem
    from app.services.kpis import compile_kpi_formula

    db_session.add(LineItem(
        account_code="4999", name="Revenue", category="Other", business_unit_id=company["bu"].id,
    ))
    db_session.commit()
    with pytest.raises(FormulaError, match=r"\[item:Revenue\] or \[category:Revenue\]"):
        compile_kpi_formula(db_session, business_unit_id=company["bu"].id, formula="[Revenue]")


def test_another_companys_lines_do_not_resolve(db_session, company):
    from app.models.business_unit import BusinessUnit
    from app.services.kpis import compile_kpi_formula

    other = BusinessUnit(name=f"KpiOther-{uuid.uuid4().hex[:6]}")
    db_session.add(other)
    db_session.commit()
    with pytest.raises(FormulaError):
        compile_kpi_formula(db_session, business_unit_id=other.id, formula="[Product Revenue]")


def test_evaluation_uses_overrides_and_skips_calculated_lines_in_categories(db_session, company):
    from app.services.kpis import evaluate_kpis

    bu = company["bu"]
    _kpi(db_session, bu, "Gross margin %", "([category:Revenue] - [COGS]) / [category:Revenue]", "percent")
    _kpi(db_session, bu, "Product revenue", "[Product Revenue]", "currency")
    db_session.commit()

    by_name = {k["name"]: k for k in evaluate_kpis(db_session, company["version"])}
    gm = by_name["Gross margin %"]
    # Revenue category = product (100, 150 overridden) + service (50, 50); the
    # calculated Gross Margin line is not double counted. COGS = 60, 80.
    assert gm["by_period"] == [
        {"period": "2026-01", "value": pytest.approx((150 - 60) / 150)},
        {"period": "2026-02", "value": pytest.approx((200 - 80) / 200)},
    ]
    assert gm["total"] == pytest.approx((350 - 140) / 350)  # ratio of totals
    assert by_name["Product revenue"]["total"] == 250.0
    assert gm["error"] is None and gm["missing"] == []


def test_kpi_on_line_without_forecast_reports_missing(db_session, company):
    from app.models.line_item import LineItem
    from app.services.kpis import evaluate_kpis

    db_session.add(LineItem(account_code="7000", name="Freight", category="OpEx", business_unit_id=company["bu"].id))
    db_session.commit()
    _kpi(db_session, company["bu"], "Freight share", "[Freight] / [category:Revenue]")
    db_session.commit()
    (kpi,) = evaluate_kpis(db_session, company["version"])
    assert kpi["total"] is None
    assert kpi["missing"] == ["[Freight]"]


def test_kpi_broken_by_rename_reports_error(db_session, company):
    from app.services.kpis import evaluate_kpis

    _kpi(db_session, company["bu"], "Product revenue", "[Product Revenue]")
    company["rev"].name = "Hardware Revenue"
    db_session.commit()
    (kpi,) = evaluate_kpis(db_session, company["version"])
    assert kpi["total"] is None
    assert "not a line item" in kpi["error"]


def test_duplicate_name_and_bad_format_rejected(db_session, company):
    _kpi(db_session, company["bu"], "Margin", "[COGS]")
    db_session.commit()
    with pytest.raises(ValueError, match="already exists"):
        _kpi(db_session, company["bu"], "margin", "[COGS]")
    with pytest.raises(ValueError, match="display_format"):
        _kpi(db_session, company["bu"], "Other", "[COGS]", fmt="pie")


@pytest.mark.asyncio
async def test_chat_skill_reports_kpis(db_session, company, seed_users, context_manager):
    from app.domain.base_skill import SkillContext
    from app.domain.skills.custom_kpis import GetCustomKpisSkill

    _kpi(db_session, company["bu"], "Gross margin %", "([category:Revenue] - [COGS]) / [category:Revenue]", "percent")
    db_session.commit()
    user = seed_users["admin"]
    user.business_unit_id = company["bu"].id
    user.is_platform_admin = False
    db_session.commit()
    context_manager.user = user
    ctx = SkillContext(
        db=db_session, context_manager=context_manager, user_id=user.id,
        user_role="admin", conversation_id="c", user=user,
    )
    result = await GetCustomKpisSkill().execute({"version_id": company["version"].id}, ctx)
    assert result.success, result.message
    assert result.content_blocks[0]["data"]["rows"][0]["value"] == "60.0%"


# --- API ------------------------------------------------------------------------

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


@pytest.fixture
def tenants():
    """Company A (admin, analyst, line items, a version) and company B (admin)."""
    from app.database import SessionLocal
    from app.models.business_unit import BusinessUnit
    from app.models.company import Company
    from app.models.forecast import ForecastLineResult, ForecastVersion
    from app.models.line_item import LineItem
    from app.models.user import Role, User

    tag = uuid.uuid4().hex[:8]
    db = SessionLocal()
    try:
        roles = {r.name: r for r in db.query(Role).all()}
        bus = {}
        for side in ("A", "B"):
            co = Company(name=f"KpiApiCo-{side}-{tag}")
            db.add(co)
            db.flush()
            bu = BusinessUnit(name=f"KpiApiBU-{side}-{tag}", company_id=co.id)
            db.add(bu)
            db.flush()
            bus[side] = bu.id
        li = LineItem(account_code=f"K-{tag}", name=f"Kpi Revenue {tag}", category="Revenue", business_unit_id=bus["A"])
        db.add(li)
        db.flush()
        version = ForecastVersion(name=f"KpiApiV-{tag}", business_unit_id=bus["A"])
        db.add(version)
        db.flush()
        db.add(ForecastLineResult(version_id=version.id, line_item_id=li.id, period="2026-01", p50=42.0))
        out = {"bu_a": bus["A"], "bu_b": bus["B"], "version": version.id, "item": li.name}
        for key, side, role in (("admin_a", "A", "admin"), ("analyst_a", "A", "analyst"), ("admin_b", "B", "admin")):
            username = f"kpi_{key}_{tag}"
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


def test_api_lifecycle_and_permissions(client, tenants):
    admin = _h(client, tenants["admin_a"])
    analyst = _h(client, tenants["analyst_a"])
    outsider = _h(client, tenants["admin_b"])
    body = {"name": "Doubled", "formula": f"[{tenants['item']}] * 2", "display_format": "currency"}

    assert client.post("/api/kpis", json=body, headers=analyst).status_code == 403
    r = client.post("/api/kpis", json=body, headers=admin)
    assert r.status_code == 201, r.text
    kpi_id = r.json()["id"]

    assert [k["name"] for k in client.get("/api/kpis", headers=analyst).json()["kpis"]] == ["Doubled"]
    r = client.get(f"/api/kpis/evaluate/{tenants['version']}", headers=analyst)
    assert r.json()["kpis"][0]["total"] == 84.0

    # Another company sees nothing and can't touch or evaluate it.
    assert client.get("/api/kpis", headers=outsider).json()["kpis"] == []
    assert client.put(f"/api/kpis/{kpi_id}", json={"name": "x"}, headers=outsider).status_code == 404
    assert client.delete(f"/api/kpis/{kpi_id}", headers=outsider).status_code == 404
    assert client.get(f"/api/kpis/evaluate/{tenants['version']}", headers=outsider).status_code == 404
    r = client.post("/api/kpis", json={**body, "business_unit_id": tenants["bu_a"]}, headers=outsider)
    assert r.status_code == 404

    r = client.put(f"/api/kpis/{kpi_id}", json={"formula": f"[{tenants['item']}] * 3"}, headers=admin)
    assert r.status_code == 200
    assert client.get(f"/api/kpis/evaluate/{tenants['version']}", headers=admin).json()["kpis"][0]["total"] == 126.0
    assert client.delete(f"/api/kpis/{kpi_id}", headers=admin).status_code == 204
    assert client.get("/api/kpis", headers=admin).json()["kpis"] == []


def test_api_rejects_bad_formulas_and_validates(client, tenants):
    admin = _h(client, tenants["admin_a"])
    r = client.post("/api/kpis", json={"name": "Bad", "formula": "[Nope] / 2"}, headers=admin)
    assert r.status_code == 400
    assert "not a line item or category" in r.json()["detail"]

    r = client.post("/api/kpis/validate", json={"formula": f"[{tenants['item']}] / [category:Revenue]"}, headers=admin)
    assert r.json() == {
        "valid": True,
        "references": [
            {"reference": f"[{tenants['item']}]", "kind": "item"},
            {"reference": "[category:Revenue]", "kind": "category"},
        ],
    }
    r = client.post("/api/kpis/validate", json={"formula": "1 +"}, headers=admin)
    assert r.json()["valid"] is False


def test_executive_dashboard_includes_custom_kpis(client, tenants):
    admin = _h(client, tenants["admin_a"])
    client.post("/api/kpis", json={"name": "Rev", "formula": f"[{tenants['item']}]"}, headers=admin)
    r = client.get(f"/api/panel/executive-dashboard/{tenants['version']}", headers=admin)
    assert r.status_code == 200, r.text
    kpis = r.json()["data"]["custom_kpis"]
    assert [(k["name"], k["total"]) for k in kpis] == [("Rev", 42.0)]
