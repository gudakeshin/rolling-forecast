"""Phase 2.3 — ensemble-by-default blending of the top-2 within-1-SE-band models."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.domain.engines.model_registry import (
    ModelComparisonResult,
    ModelRegistry,
    ModelSelectionResult,
    reset_model_registry,
)
from app.models.line_item import LineItem
from app.services.forecast_pipeline import LineForecastContext, forecast_line_item
from app.services.period_calendar import get_calendar_config


@pytest.fixture(autouse=True)
def _reset_registry():
    reset_model_registry()
    yield
    reset_model_registry()


def _make_li(account_code: str, name: str, **kwargs) -> LineItem:
    li = LineItem(
        account_code=account_code,
        name=name,
        category=kwargs.get("category", "Revenue"),
        display_order=kwargs.get("display_order", 1),
        allow_negative=kwargs.get("allow_negative", False),
        sign_convention=kwargs.get("sign_convention", "positive"),
    )
    li.id = kwargs.get("id", hash(account_code) % 10_000_000)
    return li


def _tied_selection_result() -> ModelSelectionResult:
    """Two candidates whose MASEs sit well inside the 5%-floor 1-SE band."""
    return ModelSelectionResult(
        best_model="linear",
        best_mape=5.0,
        best_mase=0.50,
        best_pinball=1.0,
        comparisons=[
            ModelComparisonResult(
                model_name="linear",
                mape=5.0,
                evaluation_time_ms=1.0,
                eligible=True,
                mase=0.50,
                pinball=1.0,
                complexity_rank=10,
                cost_class="cheap",
                fold_mases=[0.48, 0.50, 0.52],
                n_folds=3,
            ),
            ModelComparisonResult(
                model_name="ets",
                mape=5.2,
                evaluation_time_ms=1.0,
                eligible=True,
                mase=0.505,
                pinball=1.05,
                complexity_rank=20,
                cost_class="cheap",
                fold_mases=[0.49, 0.505, 0.52],
                n_folds=3,
            ),
        ],
        data_points=24,
        selection_method="rolling_origin_cv",
        selection_rule="mase_pinball_complexity",
    )


def _run_with_fake_selection(monkeypatch, *, enabled: bool):
    from app.config import settings

    monkeypatch.setattr(settings, "enable_ensemble_blending", enabled)
    registry = ModelRegistry()

    fake_result = _tied_selection_result()

    def fake_auto_select(self, series, dates, test_size=6, random_seed=42, models_to_test=None, **kwargs):
        return fake_result.best_model, fake_result.best_mape, fake_result

    monkeypatch.setattr(ModelRegistry, "auto_select", fake_auto_select)

    li = _make_li("TEST-ENS", "Ensemble Fixture", id=9010)
    n = 24
    values = pd.Series(np.arange(n, dtype=float) * 10.0 + 100.0)
    periods = [f"{2023 + (i // 12)}-{(i % 12) + 1:02d}" for i in range(n)]
    dates = pd.DatetimeIndex([pd.Timestamp(f"{p}-01") for p in periods])
    ctx = LineForecastContext(
        version_id="ens-v1",
        horizon=6,
        random_seed=42,
        model_type="auto",
        selection_rule="mase_pinball_complexity",
        is_material=True,
        cal_cfg=get_calendar_config(),
        model_registry=registry,
    )
    return forecast_line_item(None, li, values, dates, periods, ctx)  # type: ignore[arg-type]


def test_blends_top2_when_enabled(monkeypatch):
    out = _run_with_fake_selection(monkeypatch, enabled=True)

    assert not out.skipped
    assert out.selected_model is not None
    assert out.selected_model.startswith("ensemble(")
    assert "linear" in out.selected_model and "ets" in out.selected_model

    assert len(out.metadata_rows) == 1
    blend_meta = out.metadata_rows[0].parameters.get("ensemble_blend")
    assert blend_meta is not None
    assert set(blend_meta["models"]) == {"linear", "ets"}
    assert sum(blend_meta["weights"].values()) == pytest.approx(1.0, abs=1e-6)
    # Better (lower-MASE) model gets the larger weight.
    assert blend_meta["weights"]["linear"] > blend_meta["weights"]["ets"]

    assert len(out.line_rows) == 6
    for row in out.line_rows:
        assert row["model_type"] == out.selected_model
        assert row["p10"] <= row["p50"] <= row["p90"]


def test_no_blend_when_disabled(monkeypatch):
    out = _run_with_fake_selection(monkeypatch, enabled=False)

    assert not out.skipped
    assert out.selected_model == "linear"
    assert len(out.metadata_rows) == 1
    assert out.metadata_rows[0].parameters.get("ensemble_blend") is None
