"""M3 reflection MVP — heuristic detection thresholds, caps, and persistence.

Also covers the review lifecycle (promote / reject / supersede) and the *soft*
consumption path: an active heuristic may only redirect model selection on a
non-target-bearing line, and override-derived heuristics may never redirect it
at all.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.models.audit import AuditEvent
from app.models.forecast import ForecastVersion
from app.models.fx import ForecastAccuracyRecord
from app.models.heuristic import LearnedHeuristic
from app.models.override import Override
from app.services.reflection import (
    active_heuristics_for_line,
    detect_error_bias,
    detect_override_patterns,
    horizon_bucket,
    influences_selection,
    promote_heuristic,
    reject_heuristic,
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


# ── Review lifecycle ─────────────────────────────────────


def _candidate(
    db,
    *,
    line_item_id: int,
    model_type: str | None = "ets",
    kind: str = "error_bias",
    source: str = "actuals",
    horizon_bucket_: str | None = "1-3",
    effect_size: float = 8.0,
    statement: str = "Line is over-forecast",
) -> LearnedHeuristic:
    row = LearnedHeuristic(
        scope="line_item",
        line_item_id=line_item_id,
        model_type=model_type,
        horizon_bucket=horizon_bucket_,
        kind=kind,
        statement=statement,
        effect_size=effect_size,
        evidence={"cycles": 3},
        status="candidate",
        source=source,
    )
    db.add(row)
    db.commit()
    return row


def test_promote_heuristic_activates_and_audits(db_session, seed_users, seed_line_items):
    reviewer = seed_users["admin"]
    revenue = seed_line_items["REV-001"]
    candidate = _candidate(db_session, line_item_id=revenue.id)

    promoted = promote_heuristic(db_session, candidate.id, actor=reviewer)

    assert promoted.status == "active"
    assert promoted.approved_by == reviewer.id
    # Actuals-derived, so it is allowed near model selection.
    assert promoted.influences_selection is True
    assert promoted.superseded_ids == []

    audit = (
        db_session.query(AuditEvent)
        .filter(
            AuditEvent.action == "heuristic.promote",
            AuditEvent.entity_id == str(candidate.id),
        )
        .one()
    )
    assert audit.entity_type == "learned_heuristic"
    assert audit.details["influences_selection"] is True
    assert audit.details["superseded_ids"] == []


def test_promote_supersedes_other_active_heuristic_for_same_slice(
    db_session, seed_users, seed_line_items
):
    reviewer = seed_users["admin"]
    revenue = seed_line_items["REV-001"]
    opex = seed_line_items["OPEX-001"]

    first = _candidate(db_session, line_item_id=revenue.id, effect_size=8.0)
    second = _candidate(db_session, line_item_id=revenue.id, effect_size=-6.0)
    # Same line, different horizon bucket → a different slice, must survive.
    other_bucket = _candidate(
        db_session, line_item_id=revenue.id, horizon_bucket_="7-12"
    )
    # Different line entirely → untouched.
    other_line = _candidate(db_session, line_item_id=opex.id)

    promote_heuristic(db_session, first.id, actor=reviewer)
    promote_heuristic(db_session, other_bucket.id, actor=reviewer)
    promote_heuristic(db_session, other_line.id, actor=reviewer)
    promoted = promote_heuristic(db_session, second.id, actor=reviewer)

    for row in (first, second, other_bucket, other_line):
        db_session.refresh(row)
    assert promoted.superseded_ids == [first.id]
    assert first.status == "superseded"
    assert second.status == "active"
    assert other_bucket.status == "active"
    assert other_line.status == "active"


def test_reject_heuristic_from_candidate_and_active(
    db_session, seed_users, seed_line_items
):
    reviewer = seed_users["admin"]
    revenue = seed_line_items["REV-001"]

    candidate = _candidate(db_session, line_item_id=revenue.id)
    rejected = reject_heuristic(db_session, candidate.id, actor=reviewer)
    assert rejected.status == "rejected"
    assert rejected.influences_selection is False

    audit = (
        db_session.query(AuditEvent)
        .filter(
            AuditEvent.action == "heuristic.reject",
            AuditEvent.entity_id == str(candidate.id),
        )
        .one()
    )
    assert audit.details["previous_status"] == "candidate"

    # An active heuristic can be withdrawn the same way.
    active = _candidate(db_session, line_item_id=revenue.id, horizon_bucket_="4-6")
    promote_heuristic(db_session, active.id, actor=reviewer)
    assert reject_heuristic(db_session, active.id, actor=reviewer).status == "rejected"

    # Terminal states are terminal.
    with pytest.raises(ValueError):
        reject_heuristic(db_session, candidate.id, actor=reviewer)
    with pytest.raises(ValueError):
        promote_heuristic(db_session, candidate.id, actor=reviewer)
    with pytest.raises(ValueError):
        promote_heuristic(db_session, 10**9, actor=reviewer)


def test_override_derived_heuristic_promotes_but_cannot_influence_selection(
    db_session, seed_users, seed_line_items
):
    reviewer = seed_users["admin"]
    revenue = seed_line_items["REV-001"]
    override_derived = _candidate(
        db_session,
        line_item_id=revenue.id,
        kind="override_pattern",
        source="overrides",
        model_type="ets",
        horizon_bucket_=None,
        statement="Reviewers consistently adjust this line up",
    )

    promoted = promote_heuristic(db_session, override_derived.id, actor=reviewer)
    assert promoted.status == "active"
    assert promoted.influences_selection is False
    assert influences_selection(promoted) is False

    # Consumable lookups must not see it, whatever kind is asked for.
    assert active_heuristics_for_line(db_session, revenue.id) == []
    assert (
        active_heuristics_for_line(db_session, revenue.id, kind="override_pattern")
        == []
    )
    visible = active_heuristics_for_line(
        db_session, revenue.id, kind="override_pattern", consumable_only=False
    )
    assert [r.id for r in visible] == [override_derived.id]


# ── Soft consumption in the forecast pipeline ────────────


def _pipeline_context(*, models_to_test: list[str] | None = None):
    from app.domain.engines.model_registry import ModelRegistry
    from app.services.forecast_pipeline import LineForecastContext
    from app.services.period_calendar import get_calendar_config

    return LineForecastContext(
        version_id="m3-nudge-v1",
        horizon=3,
        random_seed=42,
        model_type="auto",
        models_to_test=models_to_test,
        selection_rule="mase_pinball_complexity",
        is_material=True,
        cal_cfg=get_calendar_config(),
        model_registry=ModelRegistry(),
        enable_driver_forecasting=False,
    )


def _flat_series(n: int = 30) -> tuple[pd.Series, pd.DatetimeIndex, list[str]]:
    """Near-constant history: several models land in a statistical tie."""
    rng = np.random.default_rng(17)
    periods = [f"{2022 + (i // 12)}-{(i % 12) + 1:02d}" for i in range(n)]
    values = pd.Series(500.0 + rng.normal(0, 4.0, n), dtype=float)
    dates = pd.DatetimeIndex([pd.Timestamp(f"{p}-01") for p in periods])
    return values, dates, periods


def _tied_alternative(comparison: dict) -> str | None:
    """A model comfortably inside the 1-SE band of the statistical best."""
    from app.domain.engines.model_registry import _selection_band

    rows = [
        c
        for c in comparison["comparisons"]
        if c.get("eligible") and c.get("mase") is not None
    ]
    best = next((c for c in rows if c["model"] == comparison["best_model"]), None)
    if best is None:
        return None
    folds = [m for m in (best.get("fold_mases") or []) if m is not None]
    # Halve the band so rounding in to_dict cannot push us over the real edge.
    margin = _selection_band(folds, best.get("n_folds") or 0) * 0.5
    for row in rows:
        if row["model"] != best["model"] and row["mase"] <= best["mase"] + margin:
            return row["model"]
    return None


def _forecast(db, li, values, dates, periods, models_to_test):
    from app.services.forecast_pipeline import forecast_line_item

    return forecast_line_item(
        db, li, values, dates, periods, _pipeline_context(models_to_test=models_to_test)
    )


@pytest.fixture
def stable_pipeline_settings(monkeypatch):
    """Keep the fixture history intact so fold counts stay comparable.

    Winsorizing and post-break truncation would reshape a noise series into a
    short one, which starves the slower models of folds and destroys the tie the
    nudge tests are about.
    """
    from app.config import settings

    monkeypatch.setattr(settings, "outlier_cleaning_enabled", False)
    monkeypatch.setattr(settings, "structural_break_min_post_points", 10**6)


@pytest.fixture
def tied_line(db_session, seed_line_items, stable_pipeline_settings):
    """A line whose history leaves at least two models statistically tied."""
    from app.models.line_item import LineItem

    li = LineItem(
        account_code="REV-M3-TIE",
        name="Tied Revenue",
        category="Revenue",
        display_order=99,
        allow_negative=False,
    )
    db_session.add(li)
    db_session.commit()

    values, dates, periods = _flat_series()
    models = ["linear", "ets", "theta"]
    baseline = _forecast(db_session, li, values, dates, periods, models)
    assert baseline.comparison is not None, baseline.skip_reason
    alternative = _tied_alternative(baseline.comparison)
    if alternative is None:
        pytest.skip("no statistically tied alternative model on this fixture")
    return li, values, dates, periods, models, baseline, alternative


def test_active_heuristic_is_stamped_but_does_not_move_a_target_bearing_line(
    db_session, seed_users, tied_line
):
    li, values, dates, periods, models, baseline, alternative = tied_line
    reviewer = seed_users["admin"]

    li.is_target_bearing = True
    db_session.commit()
    heuristic = _candidate(db_session, line_item_id=li.id, model_type=alternative)
    promote_heuristic(db_session, heuristic.id, actor=reviewer)

    out = _forecast(db_session, li, values, dates, periods, models)

    # Selection is untouched: a line carrying a target must not learn to hit it.
    assert out.selected_model == baseline.selected_model
    assert out.comparison["best_model"] == baseline.comparison["best_model"]
    nudge = out.comparison["heuristic_nudge"]
    assert nudge["applied"] is False
    assert nudge["reason"] == "target_bearing_line"
    assert nudge["to_model"] == alternative
    assert any("not applied (target-bearing line)" in w for w in out.warnings)

    # But it is recorded everywhere a reviewer would look.
    stamped = out.comparison["active_heuristics"]
    assert [h["id"] for h in stamped] == [heuristic.id]
    assert stamped[0]["influences_selection"] is True
    params = out.metadata_rows[0].parameters
    assert [h["id"] for h in params["active_heuristics"]] == [heuristic.id]
    assert params["heuristic_nudge"]["applied"] is False


def test_active_heuristic_nudges_selection_on_a_non_target_bearing_line(
    db_session, seed_users, tied_line
):
    li, values, dates, periods, models, baseline, alternative = tied_line
    reviewer = seed_users["admin"]

    li.is_target_bearing = False
    db_session.commit()
    heuristic = _candidate(db_session, line_item_id=li.id, model_type=alternative)
    promote_heuristic(db_session, heuristic.id, actor=reviewer)

    out = _forecast(db_session, li, values, dates, periods, models)

    assert out.selected_model == alternative
    nudge = out.comparison["heuristic_nudge"]
    assert nudge["applied"] is True
    assert nudge["reason"] == "within_1se_of_best"
    assert nudge["from_model"] == baseline.selected_model
    assert nudge["candidate_mase"] <= nudge["best_mase"] + nudge["band"]
    # Downstream consumers read best_model as "the model we used".
    assert out.comparison["best_model"] == alternative
    assert out.comparison["statistical_best_model"] == baseline.selected_model
    assert [
        c["model"] for c in out.comparison["comparisons"] if c["selected"]
    ] == [alternative]
    assert out.line_rows[0]["model_type"] == alternative
    assert any("heuristic nudge" in w for w in out.warnings)


def test_override_derived_heuristic_never_reaches_selection(
    db_session, seed_users, tied_line
):
    li, values, dates, periods, models, baseline, alternative = tied_line
    reviewer = seed_users["admin"]

    li.is_target_bearing = False
    db_session.commit()
    heuristic = _candidate(
        db_session,
        line_item_id=li.id,
        model_type=alternative,
        kind="override_pattern",
        source="overrides",
    )
    promote_heuristic(db_session, heuristic.id, actor=reviewer)

    out = _forecast(db_session, li, values, dates, periods, models)

    assert out.selected_model == baseline.selected_model
    assert "heuristic_nudge" not in out.comparison
    assert "active_heuristics" not in out.comparison


def test_heuristic_nudge_only_picks_an_eligible_statistical_tie():
    from app.domain.engines.model_registry import (
        ModelComparisonResult,
        ModelSelectionResult,
    )
    from app.services.forecast_pipeline import _heuristic_nudge

    def _cmp(name, mase, *, eligible=True):
        return ModelComparisonResult(
            model_name=name,
            mape=10.0,
            evaluation_time_ms=1.0,
            eligible=eligible,
            mase=mase,
            fold_mases=[mase, mase, mase],
            n_folds=3,
        )

    class _Heuristic:
        def __init__(self, model_type):
            self.id = 1
            self.model_type = model_type
            self.statement = "biased"

    def _result(*comparisons):
        return ModelSelectionResult(
            best_model="linear",
            best_mape=10.0,
            comparisons=list(comparisons),
            best_mase=1.0,
        )

    # ets is 2% worse than linear — inside the 5% floor on the 1-SE band.
    tie = _result(_cmp("linear", 1.0), _cmp("ets", 1.02))
    nudge = _heuristic_nudge(
        tie, [_Heuristic("ets")], selected_model="linear", allow_override=True
    )
    assert nudge is not None and nudge["applied"] and nudge["to_model"] == "ets"

    # Same tie, but the line carries a target: advise, do not apply.
    advisory = _heuristic_nudge(
        tie, [_Heuristic("ets")], selected_model="linear", allow_override=False
    )
    assert advisory is not None and advisory["applied"] is False

    # 40% worse is not a tie.
    assert (
        _heuristic_nudge(
            _result(_cmp("linear", 1.0), _cmp("ets", 1.4)),
            [_Heuristic("ets")],
            selected_model="linear",
            allow_override=True,
        )
        is None
    )
    # Tied but ineligible, unknown to the comparison, or already selected.
    assert (
        _heuristic_nudge(
            _result(_cmp("linear", 1.0), _cmp("ets", 1.0, eligible=False)),
            [_Heuristic("ets")],
            selected_model="linear",
            allow_override=True,
        )
        is None
    )
    assert (
        _heuristic_nudge(
            tie, [_Heuristic("prophet")], selected_model="linear", allow_override=True
        )
        is None
    )
    assert (
        _heuristic_nudge(
            tie, [_Heuristic("linear")], selected_model="linear", allow_override=True
        )
        is None
    )
    # A heuristic that names no model has no preference to express.
    assert (
        _heuristic_nudge(
            tie, [_Heuristic(None)], selected_model="linear", allow_override=True
        )
        is None
    )


# ── API ──────────────────────────────────────────────────


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


def test_heuristics_api_promote_and_reject():
    import os

    from fastapi.testclient import TestClient

    os.environ["SEED_DEMO_USERS"] = "true"
    os.environ["APP_ENV"] = "test"
    from app.database import Base, SessionLocal, engine
    from app.main import app
    from app.rate_limit import limiter

    Base.metadata.create_all(bind=engine)
    try:
        limiter.reset()
    except Exception:
        pass

    def _seed(**over) -> int:
        db = SessionLocal()
        try:
            row = LearnedHeuristic(
                scope="line_item",
                kind=over.pop("kind", "error_bias"),
                statement="API fixture heuristic",
                effect_size=7.5,
                status="candidate",
                source=over.pop("source", "actuals"),
                model_type=over.pop("model_type", "ets"),
                horizon_bucket=over.pop("horizon_bucket", "1-3"),
                **over,
            )
            db.add(row)
            db.commit()
            return row.id
        finally:
            db.close()

    def _headers(client, username):
        r = client.post(
            "/api/auth/login", json={"username": username, "password": username}
        )
        assert r.status_code == 200, r.text
        return {"Authorization": f"Bearer {r.json()['access_token']}"}

    with TestClient(app) as client:
        analyst = _headers(client, "analyst")
        admin = _headers(client, "admin")

        target = _seed()
        assert (
            client.post(f"/api/heuristics/{target}/promote", headers=analyst).status_code
            == 403
        )
        assert (
            client.post(f"/api/heuristics/{target}/reject", headers=analyst).status_code
            == 403
        )

        promoted = client.post(f"/api/heuristics/{target}/promote", headers=admin)
        assert promoted.status_code == 200, promoted.text
        body = promoted.json()
        assert body["status"] == "active"
        assert body["influences_selection"] is True
        assert body["approved_by"]

        # Promoting a second heuristic for the same slice retires the first.
        successor = _seed()
        second = client.post(f"/api/heuristics/{successor}/promote", headers=admin)
        assert second.status_code == 200, second.text
        assert second.json()["superseded_ids"] == [target]

        rejected = client.post(f"/api/heuristics/{successor}/reject", headers=admin)
        assert rejected.status_code == 200, rejected.text
        assert rejected.json()["status"] == "rejected"
        # Terminal state — no further transitions.
        assert (
            client.post(
                f"/api/heuristics/{successor}/promote", headers=admin
            ).status_code
            == 400
        )
        assert (
            client.post("/api/heuristics/999999999/promote", headers=admin).status_code
            == 404
        )

        # Override-derived heuristics may be promoted, but stay out of selection.
        override_derived = _seed(
            kind="override_pattern", source="overrides", model_type=None,
            horizon_bucket=None,
        )
        promoted_override = client.post(
            f"/api/heuristics/{override_derived}/promote", headers=admin
        )
        assert promoted_override.status_code == 200, promoted_override.text
        assert promoted_override.json()["status"] == "active"
        assert promoted_override.json()["influences_selection"] is False
