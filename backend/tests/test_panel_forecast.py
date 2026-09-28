"""DB-aware tests for the global panel model's orchestration layer.

Pure feature-engineering behavior is covered in test_global_gbm.py; this file
exercises build_global_panel_context end to end against a real (in-memory)
Session, including the origin-restricted driver overlay leakage guard.
"""

from __future__ import annotations

from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from app.domain.engines.model_registry import ModelRegistry
from app.models.actuals import ActualsDataset, ActualsRecord
from app.models.driver import Driver, DriverLink
from app.models.line_item import LineItem
from app.services.driver_series import upsert_driver_values
from app.services.forecast_pipeline import compute_effective_series
from app.services.panel_forecast import build_global_panel_context
from app.services.period_calendar import get_calendar_config, period_to_date

N_PERIODS = 30


def _periods(n: int = N_PERIODS, start_year: int = 2022) -> list[str]:
    return [f"{start_year + (i // 12)}-{(i % 12) + 1:02d}" for i in range(n)]


def _add_line(db, code: str, *, category: str = "Revenue", business_unit: str | None = None) -> LineItem:
    li = LineItem(
        account_code=code, name=f"{code} line", category=category,
        business_unit=business_unit, display_order=1,
    )
    db.add(li)
    db.flush()
    return li


def _add_actuals(db, line: LineItem, periods: list[str], values: list[float]) -> None:
    dataset = ActualsDataset(
        source_type="csv", source_name=f"{line.account_code}.csv",
        file_hash=f"hash_{line.account_code}", row_count=len(values),
        period_start=periods[0], period_end=periods[-1], periods_count=len(periods),
    )
    db.add(dataset)
    db.flush()
    for period, value in zip(periods, values):
        db.add(ActualsRecord(
            dataset_id=dataset.id, line_item_id=line.id, period=period,
            value=float(value), currency="USD",
        ))
    db.flush()


def _build_inputs(db, lines: list[LineItem], periods: list[str], series_by_line: dict[int, list[float]]):
    cal_cfg = get_calendar_config()
    effective_series_by_line = {}
    periods_by_line = {}
    for li in lines:
        values = pd.Series(series_by_line[li.id])
        dates = pd.DatetimeIndex([pd.Timestamp(period_to_date(p, cal_cfg)) for p in periods])
        periods_by_line[li.id] = periods
        effective_series_by_line[li.id] = compute_effective_series(li, values, dates)
    return effective_series_by_line, periods_by_line, cal_cfg


@pytest.fixture
def registry():
    return ModelRegistry()


def test_returns_none_when_panel_too_small(db_session, registry):
    periods = _periods()
    lines = [_add_line(db_session, "REV-1")]
    _add_actuals(db_session, lines[0], periods, [100.0 + i for i in range(N_PERIODS)])
    effective, periods_by_line, cal_cfg = _build_inputs(
        db_session, lines, periods, {lines[0].id: [100.0 + i for i in range(N_PERIODS)]}
    )
    panel = build_global_panel_context(
        db_session, line_items=lines, effective_series_by_line=effective,
        periods_by_line=periods_by_line, cal_cfg=cal_cfg, version_id="v1",
        horizon=6, random_seed=42, registry=registry,
    )
    assert panel is None


def test_builds_context_for_a_simple_panel(db_session, registry):
    periods = _periods()
    rng = np.random.default_rng(7)
    codes = ["REV-1", "REV-2", "COGS-1", "OPEX-1"]
    cats = ["Revenue", "Revenue", "COGS", "OpEx"]
    lines = [_add_line(db_session, c, category=cat) for c, cat in zip(codes, cats)]
    series_by_line = {}
    for li in lines:
        base = 500.0 if li.category == "Revenue" else 200.0
        vals = list(base + np.arange(N_PERIODS) * 3.0 + rng.normal(0, 5, N_PERIODS))
        series_by_line[li.id] = vals
        _add_actuals(db_session, li, periods, vals)

    effective, periods_by_line, cal_cfg = _build_inputs(db_session, lines, periods, series_by_line)
    panel = build_global_panel_context(
        db_session, line_items=lines, effective_series_by_line=effective,
        periods_by_line=periods_by_line, cal_cfg=cal_cfg, version_id="v1",
        horizon=6, random_seed=42, registry=registry,
    )

    assert panel is not None
    assert panel.n_lines == 4
    assert panel.n_training_rows > 0
    # Engineered feature families are present.
    assert "lag_1" in panel.feature_columns
    assert "period_sin" in panel.feature_columns
    assert "h" in panel.feature_columns
    assert any(c.startswith("cat_category_") for c in panel.feature_columns)
    assert any(c.startswith("driver_") for c in panel.feature_columns)

    # Every eligible line gets a CV result shaped like evaluate_cv's output.
    for li in lines:
        assert li.id in panel.per_line_cv_results
        cv = panel.per_line_cv_results[li.id]
        for key in ("mean_mape", "mean_mase", "mean_pinball", "coverage_80", "fold_residuals", "n_folds_used"):
            assert key in cv

    # predict_features_by_line has exactly `horizon` ordered rows per line.
    for li in lines:
        feat_df = panel.predict_features_by_line[li.id]
        assert len(feat_df) == 6
        assert list(feat_df["h"]) == [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]

    # Production models can actually predict.
    for q, model in panel.production_models.items():
        X = panel.predict_features_by_line[lines[0].id][panel.feature_columns]
        preds = model.predict(X)
        assert len(preds) == 6
        assert np.all(np.isfinite(preds))


def test_excludes_all_zero_and_very_sparse_lines(db_session, registry):
    periods = _periods()
    good = [_add_line(db_session, f"REV-{i}") for i in range(3)]
    zero_line = _add_line(db_session, "ZERO-1")
    sparse_line = _add_line(db_session, "SPARSE-1")

    series_by_line = {}
    for li in good:
        vals = list(300.0 + np.arange(N_PERIODS) * 2.0)
        series_by_line[li.id] = vals
        _add_actuals(db_session, li, periods, vals)

    zero_vals = [0.0] * N_PERIODS
    series_by_line[zero_line.id] = zero_vals
    _add_actuals(db_session, zero_line, periods, zero_vals)

    # Very sparse: HistoryAnalysis.is_very_sparse is a POINT COUNT threshold
    # (< 6 months), not a zero-content one — so give this line only 4 periods.
    sparse_periods = periods[:4]
    sparse_vals = [10.0, 11.0, 9.0, 12.0]
    _add_actuals(db_session, sparse_line, sparse_periods, sparse_vals)

    good_lines = good + [zero_line]
    effective, periods_by_line, cal_cfg = _build_inputs(db_session, good_lines, periods, series_by_line)
    cal_cfg_sparse = get_calendar_config()
    sparse_series = pd.Series(sparse_vals)
    sparse_dates = pd.DatetimeIndex(
        [pd.Timestamp(period_to_date(p, cal_cfg_sparse)) for p in sparse_periods]
    )
    periods_by_line[sparse_line.id] = sparse_periods
    effective[sparse_line.id] = compute_effective_series(sparse_line, sparse_series, sparse_dates)

    all_lines = good_lines + [sparse_line]
    panel = build_global_panel_context(
        db_session, line_items=all_lines, effective_series_by_line=effective,
        periods_by_line=periods_by_line, cal_cfg=cal_cfg, version_id="v1",
        horizon=6, random_seed=42, registry=registry,
    )
    assert panel is not None
    assert panel.n_lines == 3
    assert zero_line.id not in panel.predict_features_by_line
    assert sparse_line.id not in panel.predict_features_by_line


def test_held_out_fold_driver_columns_use_origin_restricted_overlay(db_session, registry):
    """Regression test for the CV leakage fix: a line with an active driver
    link must have its held-out fold windows built from build_origin_overlays
    (origin-restricted forecasts), never from actual future driver values."""
    periods = _periods()
    lines = [_add_line(db_session, f"REV-{i}") for i in range(3)]
    driven = lines[0]

    driver = Driver(key="headcount", name="Headcount", driver_type="headcount")
    db_session.add(driver)
    db_session.flush()
    upsert_driver_values(
        db_session, driver_id=driver.id,
        rows=[{"period": p, "value": 100.0 + i} for i, p in enumerate(periods)],
    )
    db_session.add(DriverLink(
        driver_id=driver.id, line_item_id=driven.id, link_type="manual",
        relation="level", lag=1, coefficient=2.0, status="active",
    ))
    db_session.flush()

    series_by_line = {}
    for li in lines:
        vals = list(300.0 + np.arange(N_PERIODS) * 2.0)
        series_by_line[li.id] = vals
        _add_actuals(db_session, li, periods, vals)

    effective, periods_by_line, cal_cfg = _build_inputs(db_session, lines, periods, series_by_line)

    with patch(
        "app.services.panel_forecast.build_origin_overlays",
        wraps=__import__("app.services.driver_forecast_cache", fromlist=["build_origin_overlays"]).build_origin_overlays,
    ) as spy:
        panel = build_global_panel_context(
            db_session, line_items=lines, effective_series_by_line=effective,
            periods_by_line=periods_by_line, cal_cfg=cal_cfg, version_id="v1",
            horizon=6, random_seed=42, registry=registry,
        )

    assert panel is not None
    # Called once per fold for the driven line (3 folds) — never for the
    # two undriven lines, which have no active links to restrict.
    assert spy.call_count == 3
    for call in spy.call_args_list:
        assert call.kwargs["driver_ids"] == [driver.id]
