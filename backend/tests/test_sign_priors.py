"""Admin-editable sign priors (migration 018).

A prior in the ``sign_priors`` table wins over the hardcoded default, an empty
table behaves exactly like the defaults, and discovery reads whatever the table
says — so an admin who flips a prior changes which candidates survive.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.models.sign_prior import SignPrior
from app.services.driver_discovery import (
    DEFAULT_SIGN_PRIORS,
    DiscoveryConfig,
    discover_drivers_for_line,
    expected_sign_for,
    resolve_sign_prior,
)
from tests.test_phase9_discovery import (
    N_PERIODS,
    _add_actuals,
    _add_driver,
    _add_line,
    _links,
    _periods,
)

TEST_CONFIG = DiscoveryConfig(placebo_draws=50, random_seed=7)


def _prior(db, driver_type: str, family: str, sign: int, **over) -> SignPrior:
    row = SignPrior(
        driver_type=driver_type,
        line_family=family,
        expected_sign=sign,
        notes=over.pop("notes", None),
        updated_by=over.pop("updated_by", None),
    )
    db.add(row)
    db.commit()
    return row


def test_without_a_session_the_defaults_still_apply(db_session):
    assert expected_sign_for("headcount", "expense") == 1
    assert expected_sign_for("macro", "revenue") is None
    # An empty table is indistinguishable from no table at all.
    assert expected_sign_for("headcount", "expense", db_session) == 1
    assert resolve_sign_prior("headcount", "expense", db_session) == (1, "default_dict")
    assert resolve_sign_prior("macro", "revenue", db_session) == (None, "none")
    assert resolve_sign_prior("headcount", None, db_session) == (None, "none")


def test_db_prior_overrides_the_default(db_session):
    _prior(db_session, "headcount", "expense", -1, notes="Automation programme")
    assert expected_sign_for("headcount", "expense", db_session) == -1
    assert resolve_sign_prior("headcount", "expense", db_session) == (
        -1,
        "sign_priors_table",
    )
    # Driver type is matched case-insensitively, like the default lookup.
    assert expected_sign_for("HeadCount", "expense", db_session) == -1
    # Other pairs are untouched.
    assert expected_sign_for("volume", "revenue", db_session) == 1


def test_db_prior_adds_an_opinion_the_defaults_do_not_hold(db_session):
    assert ("macro", "revenue") not in DEFAULT_SIGN_PRIORS
    _prior(db_session, "macro", "revenue", 1)
    assert expected_sign_for("macro", "revenue", db_session) == 1


def test_discovery_uses_the_db_prior_to_reject_a_candidate(db_session):
    """A genuine positive relation is rejected once the admin inverts the prior."""
    rng = np.random.default_rng(5)
    periods = _periods()
    line = _add_line(db_session, "REV-SP-VOLUME", category="Revenue")

    volume = 1000.0 + 25.0 * np.arange(N_PERIODS) + rng.normal(0, 30, N_PERIODS)
    y = 5000.0 + 3.0 * volume + rng.normal(0, 40, N_PERIODS)
    _add_actuals(db_session, line, periods, list(y))
    _add_driver(db_session, "units_sp", "volume", periods, list(volume))
    db_session.commit()

    baseline = discover_drivers_for_line(db_session, line.id, config=TEST_CONFIG)
    db_session.commit()
    assert baseline.summary["n_survivors"] >= 1
    survivor = next(
        c for c in baseline.summary["candidates"] if c["driver_key"] == "units_sp"
    )
    assert survivor["expected_sign"] == 1
    link = next(l for l in _links(db_session, line.id) if l.status == "candidate")
    assert (link.diagnostics or {})["sign_prior_source"] == "default_dict"

    _prior(db_session, "volume", "revenue", -1, notes="Deliberately inverted")

    inverted = discover_drivers_for_line(db_session, line.id, config=TEST_CONFIG)
    db_session.commit()

    assert inverted.summary["n_survivors"] == 0
    rejected = next(
        c for c in inverted.summary["candidates"] if c["driver_key"] == "units_sp"
    )
    assert rejected["expected_sign"] == -1
    assert rejected["reject_reason"] == "sign_prior_violated"


# ── API ──────────────────────────────────────────────────


def _wipe_sign_priors() -> None:
    """The TestClient database is a file that outlives the run — start clean."""
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        db.query(SignPrior).delete()
        db.commit()
    finally:
        db.close()


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from app.database import Base, engine
    from app.main import app
    from app.rate_limit import limiter

    Base.metadata.create_all(bind=engine)
    _wipe_sign_priors()
    # Every test here logs in; the login limiter is shared across the session.
    try:
        limiter.reset()
    except Exception:
        pass
    with TestClient(app) as c:
        yield c
    _wipe_sign_priors()


def _auth(client, username: str = "analyst") -> dict:
    login = client.post(
        "/api/auth/login", json={"username": username, "password": username}
    )
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def test_api_lists_defaults_and_upserts_overrides(client):
    headers = _auth(client, "admin")

    listing = client.get("/api/drivers/sign-priors", headers=headers)
    assert listing.status_code == 200, listing.text
    body = listing.json()
    assert body["families"] == ["expense", "revenue"]
    pairs = {(p["driver_type"], p["line_family"]): p for p in body["priors"]}
    for (driver_type, family), sign in DEFAULT_SIGN_PRIORS.items():
        assert pairs[(driver_type, family)]["expected_sign"] == sign

    updated = client.put(
        "/api/drivers/sign-priors",
        headers=headers,
        json={
            "priors": [
                {
                    "driver_type": "Headcount",
                    "line_family": "Expense",
                    "expected_sign": -1,
                    "notes": "Automation programme",
                },
                {"driver_type": "macro", "line_family": "revenue", "expected_sign": 1},
            ]
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["created"] == 2
    stored = {
        (p["driver_type"], p["line_family"]): p for p in updated.json()["priors"]
    }
    # Keys are normalized to lowercase so discovery lookups match.
    assert stored[("headcount", "expense")]["expected_sign"] == -1
    assert stored[("headcount", "expense")]["source"] == "sign_priors_table"
    assert stored[("headcount", "expense")]["updated_by"]
    assert stored[("macro", "revenue")]["expected_sign"] == 1

    # Re-sending the same pair updates in place rather than duplicating.
    again = client.put(
        "/api/drivers/sign-priors",
        headers=headers,
        json={
            "priors": [
                {"driver_type": "headcount", "line_family": "expense", "expected_sign": 1}
            ]
        },
    )
    assert again.status_code == 200, again.text
    assert again.json() == {**again.json(), "created": 0, "updated": 1}
    final = {(p["driver_type"], p["line_family"]): p for p in again.json()["priors"]}
    assert final[("headcount", "expense")]["expected_sign"] == 1

    from app.database import SessionLocal
    from app.models.audit import AuditEvent

    db = SessionLocal()
    try:
        assert (
            db.query(AuditEvent)
            .filter(AuditEvent.action == "driver.sign_priors.upsert")
            .count()
            >= 1
        )
    finally:
        db.close()


def test_api_rejects_bad_families_signs_and_duplicates(client):
    headers = _auth(client, "admin")

    def _put(priors):
        return client.put(
            "/api/drivers/sign-priors", headers=headers, json={"priors": priors}
        )

    assert _put(
        [{"driver_type": "volume", "line_family": "ebitda", "expected_sign": 1}]
    ).status_code == 400
    assert _put(
        [{"driver_type": "volume", "line_family": "revenue", "expected_sign": 0}]
    ).status_code == 400
    # Out of the -1..1 range is caught by validation, not the handler.
    assert _put(
        [{"driver_type": "volume", "line_family": "revenue", "expected_sign": 5}]
    ).status_code == 422
    assert _put(
        [
            {"driver_type": "volume", "line_family": "revenue", "expected_sign": 1},
            {"driver_type": "Volume", "line_family": "Revenue", "expected_sign": -1},
        ]
    ).status_code == 400
    assert _put([]).status_code == 422


def test_api_sign_priors_require_manage_drivers(client):
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
        if db.query(User).filter(User.username == "input_only_sp").first() is None:
            db.add(
                User(
                    email="input_sp@test.local",
                    username="input_only_sp",
                    hashed_password=pwd.hash("input_only_sp"),
                    full_name="Input",
                    role_id=role.id,
                )
            )
        db.commit()
    finally:
        db.close()

    headers = _auth(client, "input_only_sp")
    assert client.get("/api/drivers/sign-priors", headers=headers).status_code == 403
    assert (
        client.put(
            "/api/drivers/sign-priors",
            headers=headers,
            json={
                "priors": [
                    {
                        "driver_type": "volume",
                        "line_family": "revenue",
                        "expected_sign": 1,
                    }
                ]
            },
        ).status_code
        == 403
    )
