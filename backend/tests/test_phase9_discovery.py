"""Phase 9 — statistical driver discovery MVP.

Discovery must find a genuine linear driver, refuse to be fooled by independent
random walks, supersede its own prior candidates on re-run, and never produce an
``active`` link.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.models.actuals import ActualsDataset, ActualsRecord
from app.models.driver import Driver, DriverLink
from app.models.line_item import LineItem
from app.services.driver_discovery import (
    DiscoveryConfig,
    _benjamini_hochberg,
    discover_drivers_for_line,
    expected_sign_for,
)
from app.services.driver_series import upsert_driver_values

# Cheap placebo so the suite stays fast; production default is 200.
TEST_CONFIG = DiscoveryConfig(placebo_draws=50, random_seed=7)

N_PERIODS = 36


def _periods(n: int = N_PERIODS, start_year: int = 2022) -> list[str]:
    return [f"{start_year + (i // 12)}-{(i % 12) + 1:02d}" for i in range(n)]


def _add_line(db, code: str, *, category: str = "Revenue") -> LineItem:
    li = LineItem(account_code=code, name=f"{code} line", category=category, display_order=1)
    db.add(li)
    db.flush()
    return li


def _add_actuals(db, line: LineItem, periods: list[str], values: list[float]) -> None:
    dataset = ActualsDataset(
        source_type="csv",
        source_name=f"{line.account_code}.csv",
        file_hash=f"hash_{line.account_code}",
        row_count=len(values),
        period_start=periods[0],
        period_end=periods[-1],
        periods_count=len(periods),
    )
    db.add(dataset)
    db.flush()
    for period, value in zip(periods, values):
        db.add(
            ActualsRecord(
                dataset_id=dataset.id,
                line_item_id=line.id,
                period=period,
                value=float(value),
                currency="USD",
            )
        )
    db.flush()


def _add_driver(
    db, key: str, driver_type: str, periods: list[str], values: list[float]
) -> Driver:
    d = Driver(key=key, name=key.replace("_", " ").title(), driver_type=driver_type)
    db.add(d)
    db.flush()
    upsert_driver_values(
        db,
        driver_id=d.id,
        rows=[{"period": p, "value": float(v)} for p, v in zip(periods, values)],
    )
    db.flush()
    return d


def _links(db, line_item_id: int) -> list[DriverLink]:
    return (
        db.query(DriverLink)
        .filter(DriverLink.line_item_id == line_item_id)
        .order_by(DriverLink.id.asc())
        .all()
    )


@pytest.fixture
def causal_line(db_session):
    """Revenue line generated as y = 5000 + 3·volume + small noise, plus 3 decoys."""
    rng = np.random.default_rng(11)
    periods = _periods()
    line = _add_line(db_session, "REV-P9-CAUSAL", category="Revenue")

    volume = 1000.0 + 25.0 * np.arange(N_PERIODS) + rng.normal(0, 30, N_PERIODS)
    y = 5000.0 + 3.0 * volume + rng.normal(0, 40, N_PERIODS)
    _add_actuals(db_session, line, periods, list(y))
    driver = _add_driver(db_session, "units_p9", "volume", periods, list(volume))

    for i in range(3):
        noise = rng.normal(500, 50, N_PERIODS)
        _add_driver(db_session, f"decoy_p9_{i}", "macro", periods, list(noise))

    db_session.commit()
    return line, driver


def test_synthetic_causal_driver_survives_as_candidate(db_session, causal_line):
    line, driver = causal_line

    run = discover_drivers_for_line(db_session, line.id, config=TEST_CONFIG)
    db_session.commit()

    assert run.status == "completed"
    assert run.summary["n_tests"] > 0
    assert run.summary["family_size"] == run.summary["n_tests"]
    assert run.summary["n_survivors"] >= 1

    links = _links(db_session, line.id)
    assert links, "expected at least one candidate link"
    assert all(l.status == "candidate" for l in links)
    assert all(l.link_type == "discovered" for l in links)

    survivor = next(l for l in links if l.driver_id == driver.id)
    assert survivor.lag == 0
    assert survivor.coefficient == pytest.approx(3.0, rel=0.15)
    assert survivor.p_value_adj is not None and survivor.p_value_adj < 0.05
    assert survivor.n_obs >= 18
    assert survivor.discovery_run_id == run.id
    assert survivor.fit_method in {"ols", "ols_hac"}
    # Elasticity near 1 for a pass-through revenue driver
    assert survivor.elasticity is not None and survivor.elasticity > 0.05
    # Diagnostics carry the audit trail for a reviewer
    diagnostics = survivor.diagnostics or {}
    assert diagnostics["family_size"] == run.summary["family_size"]
    assert diagnostics["diff_p_value_adj"] < 0.05
    assert diagnostics["placebo_p"] is not None

    # The full ranked list is auditable, rejects included.
    keys = {c["driver_key"] for c in run.summary["candidates"]}
    assert "units_p9" in keys
    assert any(c["reject_reason"] for c in run.summary["candidates"])


def test_random_walks_produce_no_active_links(db_session):
    """Independent random walks correlate in levels — the gates must not be fooled."""
    rng = np.random.default_rng(2024)
    periods = _periods()
    line = _add_line(db_session, "REV-P9-NOISE", category="Revenue")

    y = 10_000.0 + np.cumsum(rng.normal(0, 120, N_PERIODS))
    _add_actuals(db_session, line, periods, list(y))
    for i in range(5):
        walk = 500.0 + np.cumsum(rng.normal(0, 15, N_PERIODS))
        _add_driver(db_session, f"walk_p9_{i}", "volume", periods, list(walk))
    db_session.commit()

    run = discover_drivers_for_line(db_session, line.id, config=TEST_CONFIG)
    db_session.commit()

    links = _links(db_session, line.id)
    assert not [l for l in links if l.status == "active"]
    assert all(l.status == "candidate" for l in links)
    # Cap holds even if spurious correlation were to slip past the earlier gates.
    assert len(links) <= TEST_CONFIG.max_survivors
    assert run.summary["n_survivors"] == len(links)
    assert run.summary["n_survivors"] == 0

    # Several walks look decisive in levels; differencing is what rejects them.
    candidates = run.summary["candidates"]
    assert any(
        c["p_value_adj"] is not None and c["p_value_adj"] < 0.05 for c in candidates
    ), "expected at least one spuriously significant level fit"
    assert "difference_not_corroborated" in {c["reject_reason"] for c in candidates}


def test_difference_gate_can_be_disabled(db_session):
    """The flag is honored: no test is rejected for lacking difference support."""
    rng = np.random.default_rng(2024)
    periods = _periods()
    line = _add_line(db_session, "REV-P9-NOGATE", category="Revenue")

    y = 10_000.0 + np.cumsum(rng.normal(0, 120, N_PERIODS))
    _add_actuals(db_session, line, periods, list(y))
    for i in range(5):
        walk = 500.0 + np.cumsum(rng.normal(0, 15, N_PERIODS))
        _add_driver(db_session, f"nogate_p9_{i}", "volume", periods, list(walk))
    db_session.commit()

    run = discover_drivers_for_line(
        db_session,
        line.id,
        config=DiscoveryConfig(
            placebo_draws=50,
            random_seed=7,
            require_difference_corroboration=False,
        ),
    )
    db_session.commit()

    assert run.summary["difference_corroboration"] is False
    reasons = {c["reject_reason"] for c in run.summary["candidates"]}
    assert "difference_not_corroborated" not in reasons
    # Whatever survives the remaining gates is still only a candidate.
    assert all(l.status == "candidate" for l in _links(db_session, line.id))


def test_difference_corroboration_requires_fdr_and_matching_sign():
    from app.services.driver_discovery import CandidateFit, _difference_corroborates

    def _fit(**over) -> CandidateFit:
        base = dict(
            driver_id=1,
            driver_key="k",
            driver_type="volume",
            lag=0,
            n_obs=24,
            coefficient=2.0,
            coefficient_se=0.1,
            t_stat=20.0,
            p_value=0.0,
            r2=0.9,
            fit_method="ols",
            hac_lags=None,
            elasticity=1.0,
            expected_sign=1,
        )
        base.update(over)
        return CandidateFit(**base)

    assert _difference_corroborates(
        _fit(diff_coefficient=2.1, diff_p_value_adj=0.001), 0.05
    )
    # Significant in differences but the wrong way round
    assert not _difference_corroborates(
        _fit(diff_coefficient=-2.1, diff_p_value_adj=0.001), 0.05
    )
    # Right sign, but does not clear FDR
    assert not _difference_corroborates(
        _fit(diff_coefficient=2.1, diff_p_value_adj=0.4), 0.05
    )
    # Differencing was not computable at all
    assert not _difference_corroborates(_fit(), 0.05)


def test_rerun_supersedes_prior_candidates(db_session, causal_line):
    line, _driver = causal_line

    first = discover_drivers_for_line(db_session, line.id, config=TEST_CONFIG)
    db_session.commit()
    first_ids = [l.id for l in _links(db_session, line.id)]
    assert first_ids

    second = discover_drivers_for_line(db_session, line.id, config=TEST_CONFIG)
    db_session.commit()
    assert second.id != first.id
    assert second.summary["superseded_links"] == len(first_ids)

    all_links = _links(db_session, line.id)
    old = [l for l in all_links if l.id in first_ids]
    new = [l for l in all_links if l.id not in first_ids]
    assert old and all(l.status == "superseded" for l in old)
    assert new and all(l.status == "candidate" for l in new)
    assert all(l.discovery_run_id == second.id for l in new)


def test_discovery_never_creates_active_links(db_session, causal_line):
    line, _driver = causal_line

    discover_drivers_for_line(
        db_session,
        line.id,
        config=DiscoveryConfig(placebo_draws=20, enable_placebo=False, max_survivors=10),
    )
    db_session.commit()

    discovered = (
        db_session.query(DriverLink)
        .filter(DriverLink.link_type == "discovered")
        .all()
    )
    assert discovered
    assert {l.status for l in discovered} == {"candidate"}


def test_manual_links_are_not_superseded(db_session, causal_line):
    """Human-asserted candidates survive a discovery run."""
    line, driver = causal_line
    manual = DriverLink(
        driver_id=driver.id,
        line_item_id=line.id,
        link_type="manual",
        relation="level",
        transform="level",
        lag=0,
        status="candidate",
    )
    db_session.add(manual)
    db_session.commit()

    discover_drivers_for_line(db_session, line.id, config=TEST_CONFIG)
    discover_drivers_for_line(db_session, line.id, config=TEST_CONFIG)
    db_session.commit()

    db_session.refresh(manual)
    assert manual.status == "candidate"


def test_short_series_is_refused(db_session):
    periods = _periods(12)
    line = _add_line(db_session, "REV-P9-SHORT", category="Revenue")
    _add_actuals(db_session, line, periods, [100.0 + i for i in range(12)])
    _add_driver(db_session, "short_p9", "volume", periods, [10.0 + i for i in range(12)])
    db_session.commit()

    run = discover_drivers_for_line(db_session, line.id, config=TEST_CONFIG)
    db_session.commit()

    assert run.status == "insufficient_data"
    assert run.summary["reason"] == "line_series_too_short"
    assert run.summary["min_overlap"] == 18
    assert not _links(db_session, line.id)


def test_wrong_sign_is_rejected_by_prior(db_session):
    """Headcount that moves opposite to payroll must not become a candidate."""
    rng = np.random.default_rng(5)
    periods = _periods()
    line = _add_line(db_session, "OPEX-P9-PAYROLL", category="OpEx")

    headcount = 200.0 - 2.0 * np.arange(N_PERIODS) + rng.normal(0, 1, N_PERIODS)
    payroll = 100_000.0 + 500.0 * np.arange(N_PERIODS) + rng.normal(0, 400, N_PERIODS)
    _add_actuals(db_session, line, periods, list(payroll))
    _add_driver(db_session, "hc_p9_wrong", "headcount", periods, list(headcount))
    db_session.commit()

    run = discover_drivers_for_line(db_session, line.id, config=TEST_CONFIG)
    db_session.commit()

    assert run.status == "completed"
    assert run.summary["line_family"] == "expense"
    reasons = {c["reject_reason"] for c in run.summary["candidates"]}
    assert "sign_prior_violated" in reasons
    assert not _links(db_session, line.id)


def test_benjamini_hochberg_is_monotone_and_bounded():
    raw = [0.001, 0.008, 0.039, 0.041, 0.9]
    adj = _benjamini_hochberg(raw)
    assert len(adj) == len(raw)
    assert all(0.0 <= a <= 1.0 for a in adj)
    assert all(a >= p for a, p in zip(adj, raw))
    assert adj == sorted(adj)
    assert _benjamini_hochberg([]) == []


def test_sign_priors_default_dict():
    assert expected_sign_for("headcount", "expense") == 1
    assert expected_sign_for("volume", "revenue") == 1
    # No opinion on macro series or unrecognized line families
    assert expected_sign_for("macro", "revenue") is None
    assert expected_sign_for("headcount", None) is None


def test_sign_priors_table_overrides_default(db_session):
    """See tests/test_sign_priors.py for the full admin surface."""
    from app.models.sign_prior import SignPrior

    db_session.add(
        SignPrior(driver_type="headcount", line_family="expense", expected_sign=-1)
    )
    db_session.commit()
    assert expected_sign_for("headcount", "expense", db_session) == -1
    assert expected_sign_for("volume", "revenue", db_session) == 1


# ── Skill ────────────────────────────────────────────────


def test_skill_definition_and_registration():
    from app.domain.registry import get_registry, register_all_skills
    from app.domain.skill_loader import load_all_skill_definitions

    defs = load_all_skill_definitions()
    assert "discover_drivers" in defs, "missing skill md: discover_drivers"
    assert defs["discover_drivers"].description
    assert defs["discover_drivers"].parameters
    assert defs["discover_drivers"].required_role == "manage_drivers"

    register_all_skills()
    assert "discover_drivers" in set(get_registry().list_names())


@pytest.mark.asyncio
async def test_skill_runs_and_reads_back_a_discovery_run(skill_context, causal_line):
    from app.domain.skills.discover_drivers import DiscoverDriversSkill

    line, driver = causal_line
    skill = DiscoverDriversSkill()

    result = await skill.execute(
        {"action": "run", "line_item_id": line.id, "enable_placebo": False}, skill_context
    )
    assert result.success, result.error
    assert result.data["status"] == "completed"
    assert result.data["n_survivors"] >= 1
    run_id = result.data["run_id"]

    fetched = await skill.execute({"action": "get", "run_id": run_id}, skill_context)
    assert fetched.success
    assert fetched.data["run_id"] == run_id
    assert fetched.data["link_ids"] == result.data["link_ids"]

    links = _links(skill_context.db, line.id)
    assert links and all(l.status == "candidate" for l in links)
    assert any(l.driver_id == driver.id for l in links)


@pytest.mark.asyncio
async def test_skill_requires_line_item_and_rejects_unknown_run(skill_context):
    from app.domain.skills.discover_drivers import DiscoverDriversSkill

    skill = DiscoverDriversSkill()
    assert not (await skill.execute({"action": "run"}, skill_context)).success
    assert not (
        await skill.execute({"action": "get", "run_id": "nope"}, skill_context)
    ).success
    assert not (await skill.execute({"action": "wat"}, skill_context)).success


# ── API ──────────────────────────────────────────────────


def _ensure_app_schema() -> None:
    from app.database import Base, engine

    Base.metadata.create_all(bind=engine)


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    _ensure_app_schema()
    with TestClient(app) as c:
        yield c


def _auth(client, username: str = "analyst") -> dict:
    login = client.post(
        "/api/auth/login", json={"username": username, "password": username}
    )
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def test_api_run_and_fetch_discovery(client):
    import uuid

    from app.database import SessionLocal

    suffix = uuid.uuid4().hex[:8]
    rng = np.random.default_rng(3)
    periods = _periods()
    volume = 800.0 + 18.0 * np.arange(N_PERIODS) + rng.normal(0, 20, N_PERIODS)
    y = 4000.0 + 2.5 * volume + rng.normal(0, 30, N_PERIODS)

    db = SessionLocal()
    try:
        line = _add_line(db, f"REV-P9-API-{suffix}", category="Revenue")
        _add_actuals(db, line, periods, list(y))
        _add_driver(db, f"units_p9_api_{suffix}", "volume", periods, list(volume))
        db.commit()
        line_id = line.id
    finally:
        db.close()

    headers = _auth(client, "analyst")
    r = client.post(
        "/api/drivers/discovery/run",
        headers=headers,
        json={"line_item_id": line_id, "placebo_draws": 50, "max_lag": 2},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "completed"
    assert body["summary"]["n_survivors"] >= 1
    assert body["links"], "expected candidate links in the response"
    assert all(l["status"] == "candidate" for l in body["links"])
    assert all(l["link_type"] == "discovered" for l in body["links"])

    r2 = client.get(f"/api/drivers/discovery/{body['id']}", headers=headers)
    assert r2.status_code == 200, r2.text
    assert r2.json()["id"] == body["id"]
    assert len(r2.json()["links"]) == len(body["links"])

    assert client.get("/api/drivers/discovery/does-not-exist", headers=headers).status_code == 404
    r3 = client.post(
        "/api/drivers/discovery/run", headers=headers, json={"line_item_id": 10**9}
    )
    assert r3.status_code == 404

    # Audited under driver.discovery.run
    db = SessionLocal()
    try:
        from app.models.audit import AuditEvent

        evt = (
            db.query(AuditEvent)
            .filter(
                AuditEvent.action == "driver.discovery.run",
                AuditEvent.entity_id == body["id"],
            )
            .first()
        )
        assert evt is not None
        assert evt.entity_type == "driver_discovery_run"
        assert evt.details.get("line_item_id") == line_id
        assert evt.details.get("status") == "completed"
    finally:
        db.close()


def test_api_discovery_requires_manage_drivers(client):
    from passlib.context import CryptContext

    from app.database import SessionLocal
    from app.models.user import Role, User

    pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
    db = SessionLocal()
    try:
        role = db.query(Role).filter(Role.name == "input_provider").first()
        if role is None:
            role = Role(name="input_provider", description="BU", can_input=True)
            db.add(role)
            db.flush()
        role.can_manage_drivers = False
        if db.query(User).filter(User.username == "input_only_p9").first() is None:
            db.add(
                User(
                    email="input_p9@test.local",
                    username="input_only_p9",
                    hashed_password=pwd.hash("input_only_p9"),
                    full_name="Input",
                    role_id=role.id,
                )
            )
        db.commit()
    finally:
        db.close()

    headers = _auth(client, "input_only_p9")
    r = client.post(
        "/api/drivers/discovery/run", headers=headers, json={"line_item_id": 1}
    )
    assert r.status_code == 403
