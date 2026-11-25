"""Phase B forecasting prerequisites — regression tests.

Each block pins one correctness fix that would otherwise bias a model
comparison (and, later, the Chronos-2 foundation-model backtest):

1. missing periods are reindexed onto the fiscal calendar and flagged;
2. driver history: plan values never overwrite actuals, and the window is
   never backfilled from later values;
3. the FVA naive benchmark uses the last actual at the forecast origin;
4. seasonal period comes from the fiscal calendar, not a hard-coded 12;
5. CPU-heavy fits run off the event loop, several jobs at once.
"""

from __future__ import annotations

import pandas as pd
import pytest

from app.domain.skills.generate_baseline import reindex_to_fiscal_grid
from app.services.period_calendar import CalendarType, FiscalCalendarConfig


# ── 1. Reindex missing periods ─────────────────────────────────────────────


def test_reindexing_fills_and_flags_a_missing_month():
    periods = ["2024-01", "2024-02", "2024-04", "2024-05"]
    values = [100.0, 110.0, 130.0, 140.0]

    grid, filled, missing = reindex_to_fiscal_grid(periods, values)

    assert grid == ["2024-01", "2024-02", "2024-03", "2024-04", "2024-05"]
    assert missing == ["2024-03"]
    assert filled == pytest.approx([100.0, 110.0, 120.0, 130.0, 140.0])


def test_reindexing_is_a_no_op_on_a_complete_series():
    periods = ["2024-01", "2024-02", "2024-03"]
    grid, filled, missing = reindex_to_fiscal_grid(periods, [1.0, 2.0, 3.0])
    assert grid == periods and filled == [1.0, 2.0, 3.0] and missing == []


def test_reindexing_follows_the_445_calendar_across_a_year_end():
    cfg = FiscalCalendarConfig(CalendarType.FISCAL_445)
    periods = ["FY2024-P11", "FY2025-P01", "FY2025-P02"]

    grid, filled, missing = reindex_to_fiscal_grid(periods, [10.0, 30.0, 40.0], cfg)

    assert grid == ["FY2024-P11", "FY2024-P12", "FY2025-P01", "FY2025-P02"]
    assert missing == ["FY2024-P12"]
    assert filled[1] == pytest.approx(20.0)


def test_reindexing_never_extends_beyond_the_observed_span():
    grid, _filled, missing = reindex_to_fiscal_grid(["2024-03", "2024-06"], [3.0, 6.0])
    assert grid[0] == "2024-03" and grid[-1] == "2024-06"
    assert missing == ["2024-04", "2024-05"]


def _write_gappy_actuals_csv(path, seed_line_items) -> None:
    rows = []
    for code, li in seed_line_items.items():
        if li.is_calculated:
            continue
        for month in range(24):
            if month == 5:  # 2024-06 never loaded
                continue
            year = 2024 + month // 12
            m = (month % 12) + 1
            rows.append({
                "account_code": code,
                "account_name": li.name,
                "category": li.category,
                "period": f"{year}-{m:02d}",
                "value": 1000.0 + month * 10,
                "currency": "USD",
            })
    pd.DataFrame(rows).to_csv(path, index=False)


@pytest.mark.asyncio
async def test_generate_baseline_flags_lines_with_a_missing_month(
    db_session, seed_line_items, skill_context, tmp_path
):
    from app.domain.skills.generate_baseline import GenerateBaselineSkill
    from app.domain.skills.ingest_actuals import IngestActualsSkill

    csv_path = tmp_path / "gappy.csv"
    _write_gappy_actuals_csv(csv_path, seed_line_items)
    ingested = await IngestActualsSkill().execute({"file_path": str(csv_path)}, skill_context)
    assert ingested.success, ingested.message

    result = await GenerateBaselineSkill().execute(
        {"horizon_months": 3, "model_type": "linear", "random_seed": 42, "skip_plan_check": True},
        skill_context,
    )
    assert result.success, result.message
    assert result.data["gap_filled_lines"] >= 1
    assert result.data["gap_filled_lines"] == result.data["lines_forecasted"]
