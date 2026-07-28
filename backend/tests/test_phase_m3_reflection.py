"""M3 reflection MVP — heuristic detection thresholds, caps, and persistence."""

from __future__ import annotations

from app.models.audit import AuditEvent
from app.models.forecast import ForecastVersion
from app.models.fx import ForecastAccuracyRecord
from app.models.heuristic import LearnedHeuristic
from app.models.override import Override
from app.services.reflection import (
    detect_error_bias,
    detect_override_patterns,
    horizon_bucket,
    run_reflection_pass,
)


def _accuracy_rows(
    *,
    line_item_id: int,
    cycles: int,
    bias_pct: float,
    horizon_offset: int = 2,
    model_type: str = "sarimax",
    actual: float = 100.0,
) -> list[dict]:
    """One observation per cycle with a constant signed bias."""
    return [
        {
            "line_item_id": line_item_id,
            "version_id": f"v{line_item_id}-{i}",
            "period": f"2025-{i + 1:02d}",
            "horizon_offset": horizon_offset,
            "model_type": model_type,
            "predicted_p50": actual * (1 + bias_pct / 100.0),
            "actual": actual,
        }
        for i in range(cycles)
    ]


def test_horizon_bucketing():
    assert horizon_bucket(1) == "1-3"
    assert horizon_bucket(3) == "1-3"
    assert horizon_bucket(5) == "4-6"
    assert horizon_bucket(12) == "7-12"
    assert horizon_bucket(18) == "13+"
    assert horizon_bucket(None) == "unknown"


def test_two_cycles_yield_no_error_bias_candidates():
    records = _accuracy_rows(line_item_id=1, cycles=2, bias_pct=12.0)
    assert detect_error_bias(records) == []


def test_three_cycles_yield_error_bias_candidate():
    records = _accuracy_rows(line_item_id=1, cycles=3, bias_pct=12.0)
    candidates = detect_error_bias(records)
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate["kind"] == "error_bias"
    assert candidate["line_item_id"] == 1
    assert candidate["horizon_bucket"] == "1-3"
    assert candidate["source"] == "actuals"
    assert candidate["effect_size"] > 0  # over-forecast
    assert candidate["evidence"]["cycles"] == 3
    assert candidate["evidence"]["direction"] == "over"
    assert "over-forecast" in candidate["statement"]


def test_small_and_inconsistent_bias_is_ignored():
    # Mean bias below the materiality floor.
    assert detect_error_bias(_accuracy_rows(line_item_id=2, cycles=4, bias_pct=0.5)) == []

    # Alternating signs: mean is large but direction is not consistent.
    noisy = []
    for i in range(6):
        sign = 1 if i % 2 == 0 else -1
        noisy.append(
            {
                "line_item_id": 3,
                "version_id": f"v-{i}",
                "period": f"2025-{i + 1:02d}",
                "horizon_offset": 2,
                "model_type": "sarimax",
                "predicted_p50": 100.0 + sign * 30.0,
                "actual": 100.0,
            }
        )
    noisy.append(
        {
            "line_item_id": 3,
            "version_id": "v-6",
            "period": "2025-07",
            "horizon_offset": 2,
            "model_type": "sarimax",
            "predicted_p50": 160.0,
            "actual": 100.0,
        }
    )
    assert detect_error_bias(noisy) == []


def test_error_bias_candidates_capped_at_five_and_ranked_by_effect():
    records: list[dict] = []
    for line_item_id in range(1, 9):
        records.extend(
            _accuracy_rows(line_item_id=line_item_id, cycles=3, bias_pct=5.0 + line_item_id)
        )
    candidates = detect_error_bias(records)
    assert len(candidates) == 5
    effects = [abs(c["effect_size"]) for c in candidates]
    assert effects == sorted(effects, reverse=True)
    # Largest bias (line item 8) survives the cap; smallest (line item 1) does not.
    assert {c["line_item_id"] for c in candidates} == {4, 5, 6, 7, 8}


def test_override_patterns_need_min_cycles_and_same_sign():
    def _ovr(version, line_item_id, original, new):
        return {
            "line_item_id": line_item_id,
            "version_id": version,
            "period": "2026-01",
            "original_model_value": original,
            "override_value": new,
        }

    two_cycles = [_ovr("v1", 5, 100.0, 110.0), _ovr("v2", 5, 100.0, 108.0)]
    assert detect_override_patterns(two_cycles) == []

    three_cycles = two_cycles + [_ovr("v3", 5, 100.0, 112.0)]
    candidates = detect_override_patterns(three_cycles)
    assert len(candidates) == 1
    assert candidates[0]["kind"] == "override_pattern"
    assert candidates[0]["source"] == "overrides"
    assert candidates[0]["evidence"]["direction"] == "up"
    assert candidates[0]["effect_size"] > 0

    # Opposite-direction adjustments do not aggregate into one pattern.
    mixed = three_cycles + [
        _ovr("v4", 5, 100.0, 90.0),
        _ovr("v5", 5, 100.0, 92.0),
    ]
    directions = {c["evidence"]["direction"] for c in detect_override_patterns(mixed)}
    assert directions == {"up"}


def test_run_reflection_pass_persists_candidates(db_session, seed_users, seed_line_items):
    analyst = seed_users["analyst"]
    revenue = seed_line_items["REV-001"]
    opex = seed_line_items["OPEX-001"]
    opex.is_target_bearing = False
    db_session.flush()

    for i in range(3):
        version = ForecastVersion(
            name=f"FC-2026-{i + 1:02d}-v1",
            base_period=f"2025-{i + 1:02d}",
            created_by=analyst.id,
        )
        db_session.add(version)
        db_session.flush()
        for line_item, predicted in ((revenue, 115.0), (opex, 140.0)):
            db_session.add(
                ForecastAccuracyRecord(
                    version_id=version.id,
                    line_item_id=line_item.id,
                    period=f"2025-{i + 4:02d}",
                    horizon_offset=3,
                    predicted_p50=predicted,
                    actual=100.0,
                    absolute_error=abs(predicted - 100.0),
                    pct_error=abs(predicted - 100.0),
                    model_type="sarimax",
                )
            )
        db_session.add(
            Override(
                version_id=version.id,
                line_item_id=revenue.id,
                period="2026-01",
                original_model_value=100.0,
                override_value=110.0,
                reason="Pipeline coverage is stronger than the model assumes",
                user_id=analyst.id,
            )
        )
    db_session.commit()

    summary = run_reflection_pass(db_session, actor=analyst)
    assert summary["created"] == 2
    assert summary["error_bias"] == 1
    assert summary["override_pattern"] == 1

    rows = db_session.query(LearnedHeuristic).all()
    assert len(rows) == 2
    assert {r.status for r in rows} == {"candidate"}
    assert {r.line_item_id for r in rows} == {revenue.id}
    assert all(r.proposed_at is not None and r.review_by is not None for r in rows)
    # Non-target-bearing lines are excluded from learning.
    assert opex.id not in {r.line_item_id for r in rows}

    audit = (
        db_session.query(AuditEvent)
        .filter(AuditEvent.action == "reflection.pass.run")
        .one()
    )
    assert audit.details["created"] == 2

    # Re-running refreshes the open candidates instead of duplicating them.
    rerun = run_reflection_pass(db_session, actor=analyst)
    assert rerun["created"] == 0
    assert rerun["updated"] == 2
    assert db_session.query(LearnedHeuristic).count() == 2


def test_run_reflection_pass_with_two_cycles_creates_nothing(
    db_session, seed_users, seed_line_items
):
    analyst = seed_users["analyst"]
    revenue = seed_line_items["REV-001"]
    for i in range(2):
        version = ForecastVersion(name=f"FC-short-{i}", base_period="2025-01")
        db_session.add(version)
        db_session.flush()
        db_session.add(
            ForecastAccuracyRecord(
                version_id=version.id,
                line_item_id=revenue.id,
                period=f"2025-{i + 4:02d}",
                horizon_offset=3,
                predicted_p50=120.0,
                actual=100.0,
                absolute_error=20.0,
                pct_error=20.0,
                model_type="sarimax",
            )
        )
    db_session.commit()

    summary = run_reflection_pass(db_session, actor=analyst)
    assert summary["detected"] == 0
    assert summary["created"] == 0
    assert db_session.query(LearnedHeuristic).count() == 0


def test_heuristics_api_requires_reviewer_to_run():
    import os

    from fastapi.testclient import TestClient

    os.environ["SEED_DEMO_USERS"] = "true"
    os.environ["APP_ENV"] = "test"
    from app.main import app
    from app.rate_limit import limiter

    try:
        limiter.reset()
    except Exception:
        pass

    def _headers(client, username):
        r = client.post(
            "/api/auth/login", json={"username": username, "password": username}
        )
        assert r.status_code == 200, r.text
        return {"Authorization": f"Bearer {r.json()['access_token']}"}

    with TestClient(app) as client:
        analyst = _headers(client, "analyst")
        admin = _headers(client, "admin")

        listing = client.get("/api/heuristics", headers=analyst, params={"status": "candidate"})
        assert listing.status_code == 200, listing.text
        assert "heuristics" in listing.json()

        assert client.get(
            "/api/heuristics", headers=analyst, params={"kind": "nonsense"}
        ).status_code == 400

        assert client.post("/api/heuristics/run", headers=analyst, json={}).status_code == 403

        run = client.post("/api/heuristics/run", headers=admin, json={"min_cycles": 3})
        assert run.status_code == 200, run.text
        assert "created" in run.json()
