"""Behavioural checks for enhancement-plan items 2.2 and 3.1 that had no direct test."""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.domain.engines.croston import _CROSTON_ALPHA_GRID, _TSB_ALPHA_GRID, _croston_sba, _tsb
from app.domain.engines.ets import ETSModel


def _proportional_seasonal(n: int = 48) -> tuple[pd.Series, pd.DatetimeIndex]:
    """Revenue whose seasonal swing grows with its level (the plan's motivating shape)."""
    t = np.arange(n)
    level = 1000.0 * (1.03 ** t)
    season = 1 + 0.35 * np.sin(2 * np.pi * t / 12)
    noise = 1 + np.random.default_rng(0).normal(0, 0.01, n)
    return pd.Series(level * season * noise), pd.DatetimeIndex(pd.date_range("2022-01", periods=n, freq="MS"))


def test_ets_grid_offers_multiplicative_and_log_specs_for_positive_data():
    specs = ETSModel()._candidate_specs(n=48, m=12, positive=True)
    assert any(s["error"] == "mul" for s in specs)
    assert any(s["seasonal"] == "mul" for s in specs)
    assert any(s["transform"] == "log" for s in specs)
    # Non-positive data must never get multiplicative or log components.
    neg = ETSModel()._candidate_specs(n=48, m=12, positive=False)
    assert not any(s["error"] == "mul" or s["seasonal"] == "mul" or s["transform"] == "log" for s in neg)


def test_ets_picks_a_proportional_spec_on_proportional_seasonality():
    values, dates = _proportional_seasonal()
    params = ETSModel().fit(values, dates)
    chosen = (params.get("error"), params.get("seasonal"), params.get("transform"))
    assert chosen[1] == "mul" or chosen[2] == "log" or chosen[0] == "mul", chosen


def test_croston_alpha_is_searched_not_fixed():
    # Demand that shifts level: a heavier weight on recent sizes fits better than 0.1.
    y = np.array([0, 5, 0, 0, 5, 0, 0, 0, 40, 0, 45, 0, 0, 50, 0, 48, 0, 0, 52, 0], dtype=float)
    *_, alpha = _croston_sba(y)
    assert alpha in _CROSTON_ALPHA_GRID and alpha != 0.1


def test_tsb_alpha_beta_are_searched():
    y = np.array([0, 0, 0, 3, 0, 0, 0, 0, 3, 0, 4, 4, 0, 5, 4, 0, 5, 5, 4, 5], dtype=float)
    *_, alpha, beta = _tsb(y)
    assert alpha in _TSB_ALPHA_GRID
    assert (alpha, beta) != (0.1, 0.1)


def test_scheduled_jobs_are_registered_with_the_worker():
    """Plan 3.1: the refresh, pull, report and sweep jobs run on a schedule."""
    from app.workers.arq_worker import WorkerSettings

    names = {job.name for job in WorkerSettings.cron_jobs}
    assert {
        "cron:scheduled_integration_sync",
        "cron:scheduled_driver_freshness_sweep",
        "cron:scheduled_accuracy_report",
        "cron:scheduled_driver_overdue_sweep",
        "cron:scheduled_anomaly_sweep",
    } <= names
