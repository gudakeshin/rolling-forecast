"""Phase C: Chronos-2 behind the fm-service sidecar, dark by default.

The HTTP service is mocked with ``httpx.MockTransport`` installed on
``foundation_client._DEFAULT_TRANSPORT``; nothing here needs torch or a
running container (the real container has its own contract test in CI).
"""

from __future__ import annotations

import json

import httpx
import numpy as np
import pandas as pd
import pytest

from app.config import settings
from app.domain.engines.base_model import cv_fold_horizon, cv_fold_windows, cv_n_folds
from app.domain.engines.chronos2 import (
    CHRONOS2_MIN_POINTS,
    CHRONOS2_NAME,
    FoundationPanelContext,
    QuantileForecast,
)
from app.domain.engines.global_gbm import GlobalPanelContext
from app.domain.engines.model_registry import (
    ModelRegistry,
    get_model_registry,
    registry_for_analysis,
    reset_model_registry,
)
from app.services import foundation_client
from app.services.foundation_client import (
    CircuitBreaker,
    FoundationServiceUnavailable,
    forecast_batch,
    reset_circuit_breaker,
)
from app.services.foundation_forecast import score_line_cv

PROVENANCE = {
    "model_id": "amazon/chronos-2",
    "model_revision": "29ec3766d36d6f73f0696f85560a422f50e8498c",
    "service_version": "1.0.0",
    "library_version": "2.3.2",
    "quantiles": [0.1, 0.5, 0.9],
}


class FakeService:
    """Stands in for fm-service: naive-ish quantiles from each target's last value."""

    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.requests: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.fail:
            raise httpx.ConnectError("connection refused", request=request)
        body = json.loads(request.content)
        self.requests.append(body)
        forecasts = []
        for s in body["series"]:
            last = float(s["target"][-1])
            h = int(s["prediction_length"])
            forecasts.append({
                "id": s["id"],
                "p10": [last * 0.95] * h,
                "p50": [last] * h,
                "p90": [last * 1.05] * h,
            })
        return httpx.Response(200, json={"model": PROVENANCE, "forecasts": forecasts})


@pytest.fixture
def fm_on(monkeypatch):
    monkeypatch.setattr(settings, "enable_foundation_models", True)
    reset_model_registry()
    reset_circuit_breaker()
    yield
    reset_model_registry()
    reset_circuit_breaker()


def _install(monkeypatch, service: FakeService) -> None:
    monkeypatch.setattr(
        foundation_client, "_DEFAULT_TRANSPORT", httpx.MockTransport(service.handler)
    )


# ── gate ────────────────────────────────────────────────────────────────────


def test_gate_is_off_by_default():
    from app.config import Settings
    from app.services.analysis_profile import ANALYSIS_PROFILE_FIELDS

    assert Settings.model_fields["enable_foundation_models"].default is False
    assert "enable_foundation_models" in ANALYSIS_PROFILE_FIELDS
    reset_model_registry()
    try:
        assert CHRONOS2_NAME not in get_model_registry().list_models()
        assert CHRONOS2_NAME in get_model_registry(enable_foundation_models=True).list_models()
    finally:
        reset_model_registry()


def test_company_switch_turns_it_on(db_session, seed_business_unit, seed_users):
    from app.services.analysis_profile import resolve_analysis_settings, update_analysis_profile

    reset_model_registry()
    try:
        before = resolve_analysis_settings(db_session, business_unit_id=seed_business_unit.id)
        assert before.enable_foundation_models is False
        assert CHRONOS2_NAME not in registry_for_analysis(before).list_models()
        update_analysis_profile(
            db_session,
            business_unit_id=seed_business_unit.id,
            patch={"enable_foundation_models": True},
            actor=seed_users["admin"],
        )
        after = resolve_analysis_settings(db_session, business_unit_id=seed_business_unit.id)
        assert after.enable_foundation_models is True
        assert CHRONOS2_NAME in registry_for_analysis(after).list_models()
    finally:
        reset_model_registry()


def test_capabilities():
    caps = ModelRegistry(enable_foundation_models=True).get(CHRONOS2_NAME).capabilities
    assert caps.cost_class == "expensive"
    assert caps.complexity_rank == 47
    assert caps.supports_exog is True
    assert caps.is_panel_model is True


# ── end-to-end through generate_baseline ───────────────────────────────────


async def _generate(skill_context, seed_actuals):
    from app.domain.skills.generate_baseline import GenerateBaselineSkill

    skill_context.context_manager.set_memory("last_dataset_id", seed_actuals.id)
    return await GenerateBaselineSkill().execute(
        {"horizon_months": 3, "model_type": "auto", "random_seed": 42, "skip_plan_check": True},
        skill_context,
    )


def _version_metas(db_session, version_id):
    from app.models.forecast import ForecastLineResult, ModelMetadata

    return (
        db_session.query(ModelMetadata)
        .join(ForecastLineResult, ModelMetadata.line_result_id == ForecastLineResult.id)
        .filter(ForecastLineResult.version_id == version_id)
        .all()
    )


@pytest.mark.asyncio
async def test_service_called_once_per_run_and_provenance_stored(
    db_session, seed_actuals, skill_context, fm_on, monkeypatch
):
    from app.models.forecast import ForecastVersion

    service = FakeService()
    _install(monkeypatch, service)

    result = await _generate(skill_context, seed_actuals)
    assert result.success, result.message

    # Every line and every CV origin in ONE request.
    assert len(service.requests) == 1
    ids = [s["id"] for s in service.requests[0]["series"]]
    line_ids = {i.split("@")[0] for i in ids}
    assert len(line_ids) == 5  # the five non-calculated seeded lines
    assert len(ids) > len(line_ids)  # production + CV origins per line
    # Group ids never mix contexts that end at different periods.
    for s in service.requests[0]["series"]:
        assert s["group_id"].endswith("@" + s["id"].split("@", 1)[1])

    version = db_session.get(ForecastVersion, result.data["version_id"])
    prov = version.foundation_model_provenance
    assert prov["status"] == "ok"
    assert prov["model_id"] == PROVENANCE["model_id"]
    assert prov["model_revision"] == PROVENANCE["model_revision"]
    assert prov["service_version"] == PROVENANCE["service_version"]

    metas = _version_metas(db_session, version.id)
    assert metas
    for m in metas:
        note = m.parameters["foundation_model"]
        assert note["status"] == "ok"
        assert note["model_revision"] == PROVENANCE["model_revision"]


@pytest.mark.asyncio
async def test_service_down_run_completes_with_skip_reason(
    db_session, seed_actuals, skill_context, fm_on, monkeypatch
):
    from app.models.forecast import ForecastLineResult, ForecastVersion

    _install(monkeypatch, FakeService(fail=True))

    result = await _generate(skill_context, seed_actuals)
    assert result.success, result.message
    vid = result.data["version_id"]
    rows = db_session.query(ForecastLineResult).filter(ForecastLineResult.version_id == vid).all()
    assert len(rows) == 5 * 3
    assert all(r.model_type != CHRONOS2_NAME for r in rows)

    version = db_session.get(ForecastVersion, vid)
    assert version.foundation_model_provenance["status"] == "unavailable"
    assert "unreachable" in version.foundation_model_provenance["skip_reason"]

    metas = _version_metas(db_session, vid)
    assert metas
    for m in metas:
        note = m.parameters["foundation_model"]
        assert note["status"] == "unavailable"
        assert "unreachable" in note["skip_reason"]


# ── client resilience ──────────────────────────────────────────────────────


def _one_series():
    return [{"id": "1@2025-12", "target": [1.0, 2.0, 3.0], "prediction_length": 2}]


def test_circuit_breaker_opens_and_fails_fast(fm_on, monkeypatch):
    monkeypatch.setattr(settings, "fm_circuit_failure_threshold", 2)
    reset_circuit_breaker()
    service = FakeService(fail=True)
    calls = {"n": 0}

    def counting(request):
        calls["n"] += 1
        return service.handler(request)

    transport = httpx.MockTransport(counting)
    for _ in range(2):
        with pytest.raises(FoundationServiceUnavailable):
            forecast_batch(_one_series(), base_url="http://fm", transport=transport)
    assert calls["n"] == 2
    with pytest.raises(FoundationServiceUnavailable, match="circuit open"):
        forecast_batch(_one_series(), base_url="http://fm", transport=transport)
    assert calls["n"] == 2  # no network call while open


def test_circuit_breaker_half_opens_after_reset():
    breaker = CircuitBreaker(failure_threshold=1, reset_seconds=0.0)
    breaker.record_failure()
    assert breaker.is_open
    breaker.before_call()  # reset elapsed: probe allowed
    breaker.record_success()
    assert not breaker.is_open


def test_timeout_becomes_skip_reason(fm_on):
    def slow(request):
        raise httpx.ReadTimeout("timed out", request=request)

    with pytest.raises(FoundationServiceUnavailable, match="timed out"):
        forecast_batch(
            _one_series(), base_url="http://fm", timeout_seconds=1, transport=httpx.MockTransport(slow)
        )


def test_missing_series_in_response_is_unavailable(fm_on):
    def partial(request):
        return httpx.Response(200, json={"model": PROVENANCE, "forecasts": []})

    with pytest.raises(FoundationServiceUnavailable, match="missing"):
        forecast_batch(_one_series(), base_url="http://fm", transport=httpx.MockTransport(partial))


# ── selection: the paired-SE band ──────────────────────────────────────────


def _trend_series(n: int = 48, seed: int = 7, noise: float = 25.0, season: float = 40.0):
    rng = np.random.default_rng(seed)
    t = np.arange(n)
    values = pd.Series(1000 + 8.0 * t + season * np.sin(2 * np.pi * t / 12) + rng.normal(0, noise, n))
    dates = pd.date_range("2022-01-01", periods=n, freq="MS")
    periods = [d.strftime("%Y-%m") for d in dates]
    return values, dates, periods


def _small_registry() -> ModelRegistry:
    reg = ModelRegistry(
        enable_benchmark_models=False, enable_global_gbm_model=False, enable_foundation_models=True
    )
    reg._models = {k: v for k, v in reg._models.items() if k in ("linear", "ets", CHRONOS2_NAME)}
    return reg


def _panel_with(forecasts_by_origin, values, periods, horizon):
    """A FoundationPanelContext for line 1, CV scored exactly as a run would."""
    n = len(values)
    fh = cv_fold_horizon(horizon, n)
    windows = cv_fold_windows(
        n,
        min_data_points=CHRONOS2_MIN_POINTS,
        n_folds=cv_n_folds(n, min_data_points=CHRONOS2_MIN_POINTS, fold_horizon=fh),
        fold_horizon=fh,
    )
    forecasts = {}
    for train_end, test_end in windows:
        lo, mid, hi = forecasts_by_origin(train_end, test_end)
        forecasts[(1, periods[train_end - 1])] = QuantileForecast(lo, mid, hi)
    last = np.full(horizon, float(values.iloc[-1]))
    forecasts[(1, periods[-1])] = QuantileForecast(last * 0.9, last, last * 1.1)
    plan = {"values": values.to_numpy(float), "periods": periods, "windows": windows, "fold_horizon": fh}
    fm = FoundationPanelContext(
        status="ok",
        provenance=dict(PROVENANCE),
        forecasts=forecasts,
        production_origin_by_line={1: periods[-1]},
        per_line_cv_results={1: score_line_cv(1, plan, forecasts)},
    )
    panel = GlobalPanelContext.empty()
    panel.foundation = fm
    return panel, windows


def _compare(reg, values, dates, panel, horizon):
    return reg.compare_models(
        values, dates, is_material=True, panel=panel, line_item_id=1, forecast_horizon=horizon
    )


def test_wins_only_outside_the_paired_band():
    """Chronos-2 marginally ahead on average but noisy fold-by-fold stays inside
    the paired band, so Occam keeps the simpler model; clearly better on every
    fold, it wins."""
    values, dates, periods = _trend_series()
    horizon = 12
    reg = _small_registry()
    baseline = reg.compare_models(
        values, dates, is_material=True, forecast_horizon=horizon,
        models_to_test=["linear", "ets"],
    )
    simple = baseline.best_model
    assert simple in ("linear", "ets")

    def simple_fold(train_end, test_end):
        out = reg.fit_and_predict(simple, values.iloc[:train_end], dates[:train_end], test_end - train_end)
        return out.point_forecast

    # Case 1: the simple model's own fold forecasts, nudged 6% toward the
    # actuals on one fold and 5% away on another — a slightly lower average
    # MASE that is not a real, paired improvement.
    nudges = iter([0.06, -0.05, 0.06, -0.05, 0.06])

    def near_tie(train_end, test_end):
        actual = values.iloc[train_end:test_end].to_numpy(float)
        base = simple_fold(train_end, test_end)
        mid = base + next(nudges) * (actual - base)
        return mid * 0.97, mid, mid * 1.03

    panel, windows = _panel_with(near_tie, values, periods, horizon)
    assert len(windows) >= 2
    res = _compare(reg, values, dates, panel, horizon)
    by = {c.model_name: c for c in res.comparisons}
    chronos = by[CHRONOS2_NAME]
    assert chronos.eligible and chronos.error is None and not chronos.skipped_budget
    # Paired with the per-series folds (same windows, same fold length).
    assert chronos.fold_horizon == by[simple].fold_horizon
    assert len(chronos.fold_mases) == len(by[simple].fold_mases)
    assert chronos.mase <= by[simple].mase * 1.01  # at least as good on average ...
    assert res.best_model != CHRONOS2_NAME  # ... but inside the band: Occam wins

    # Case 2: near-perfect on every fold — outside any band, so it is selected.
    def strong(train_end, test_end):
        actual = values.iloc[train_end:test_end].to_numpy(float)
        return actual * 0.98, actual * 1.001, actual * 1.02

    panel, _ = _panel_with(strong, values, periods, horizon)
    res = _compare(reg, values, dates, panel, horizon)
    assert res.best_model == CHRONOS2_NAME
    out = reg.fit_and_predict(CHRONOS2_NAME, values, dates, horizon, panel=panel, line_item_id=1)
    assert out.parameters["model_revision"] == PROVENANCE["model_revision"]
    assert np.all(out.lower_bound <= out.point_forecast)
    assert np.all(out.point_forecast <= out.upper_bound)


def test_goes_through_two_stage_cost_gate():
    """Not material, a well-behaved series: expensive models are screened out
    before evaluation — Chronos-2 included, with no special case."""
    values, dates, periods = _trend_series(noise=1.0, season=0.0)
    reg = _small_registry()
    panel, _ = _panel_with(
        lambda a, b: (values.iloc[a:b].to_numpy(float),) * 3, values, periods, 12
    )
    res = reg.compare_models(
        values, dates, is_material=False, panel=panel, line_item_id=1, forecast_horizon=12
    )
    cheap_best = min(c.mase for c in res.comparisons if c.cost_class == "cheap")
    assert cheap_best < 1.0  # precondition: the cheap screen is good enough
    chronos = next(c for c in res.comparisons if c.model_name == CHRONOS2_NAME)
    assert chronos.skipped_budget
    assert res.best_model != CHRONOS2_NAME


def test_unavailable_context_marks_model_ineligible_with_reason():
    values, dates, _ = _trend_series()
    reg = _small_registry()
    panel = GlobalPanelContext.empty()
    panel.foundation = FoundationPanelContext.unavailable("fm-service unreachable: ConnectError")
    res = _compare(reg, values, dates, panel, 12)
    chronos = next(c for c in res.comparisons if c.model_name == CHRONOS2_NAME)
    assert chronos.eligible is False
    assert "unreachable" in (chronos.error or "")
    assert res.best_model in ("linear", "ets")


def test_bare_fit_without_panel_raises():
    model = ModelRegistry(enable_foundation_models=True).get(CHRONOS2_NAME)
    values, dates, _ = _trend_series()
    with pytest.raises(RuntimeError):
        model.fit(values, dates)


def test_registry_gate_skips_batch_entirely(db_session, monkeypatch):
    """With the gate off the foundation builder is never invoked."""
    from app.services import panel_forecast

    called = []
    monkeypatch.setattr(
        "app.services.foundation_forecast.build_foundation_context",
        lambda *a, **k: called.append(1),
    )
    reg = ModelRegistry(enable_foundation_models=False)
    out = panel_forecast.build_global_panel_context(
        db_session, line_items=[], effective_series_by_line={}, periods_by_line={},
        cal_cfg=None, version_id="v", horizon=3, random_seed=42, registry=reg,
    )
    assert out is None
    assert called == []


def test_range_blend_builds_batch_without_making_chronos2_a_candidate(db_session, monkeypatch):
    """The range blend needs Chronos-2's paths, not Chronos-2 in selection."""
    from app.services import panel_forecast

    sentinel = FoundationPanelContext.unavailable("stub")
    monkeypatch.setattr(settings, "foundation_range_blend_enabled", True)
    monkeypatch.setattr(
        "app.services.foundation_forecast.build_foundation_context", lambda *a, **k: sentinel
    )
    reg = ModelRegistry(enable_foundation_models=False)
    out = panel_forecast.build_global_panel_context(
        db_session, line_items=[], effective_series_by_line={}, periods_by_line={},
        cal_cfg=None, version_id="v", horizon=3, random_seed=42, registry=reg,
    )
    assert out is not None and out.foundation is sentinel
    assert reg.get(CHRONOS2_NAME) is None
