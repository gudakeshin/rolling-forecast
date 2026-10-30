"""Holdout backtest for the forecasting roster (enhancement plan, Phase 2).

The plan's ship criterion for new model behavior: the new roster must win on
MASE *and* not regress pinball loss. This runs the real per-line pipeline
(`forecast_line_item`: selection, blending, conformal calibration) on each
series with its last ``--holdout`` months withheld, under four settings, and
scores the withheld months:

    baseline      single CV winner per line (current defaults)
    +blend        enable_ensemble_blending
    +gbm          enable_global_gbm_model (panel built on the training window)
    +blend+gbm    both

MASE and pinball are scaled by each series' in-sample naive MAE so they can be
averaged across series of very different sizes.

    ./venv/bin/python scripts/backtest_roster.py ../test_actuals_data.csv
"""

from __future__ import annotations

import argparse
import dataclasses
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.database import Base  # noqa: E402
from app.domain.engines.model_registry import registry_for_analysis  # noqa: E402
from app.models.line_item import LineItem  # noqa: E402
from app.services.analysis_profile import resolve_analysis_settings  # noqa: E402
from app.services.forecast_pipeline import (  # noqa: E402
    LineForecastContext,
    compute_effective_series,
    forecast_line_item,
)
from app.services.panel_forecast import build_global_panel_context  # noqa: E402
from app.services.period_calendar import get_calendar_config, period_to_date  # noqa: E402

CONFIGS = {
    "baseline": {"enable_ensemble_blending": False, "enable_global_gbm_model": False},
    "+blend": {"enable_ensemble_blending": True, "enable_global_gbm_model": False},
    "+gbm": {"enable_ensemble_blending": False, "enable_global_gbm_model": True},
    "+blend+gbm": {"enable_ensemble_blending": True, "enable_global_gbm_model": True},
}


def load_series(path: Path) -> dict[str, tuple[str, pd.DataFrame]]:
    df = pd.read_csv(path)
    out = {}
    for code, g in df.groupby("account_code"):
        g = g.sort_values("period")
        name = str(g["account_name"].iloc[0]) if "account_name" in g else str(code)
        out[str(code)] = (name, g[["period", "value"]].reset_index(drop=True))
    return out


def naive_scale(train: np.ndarray) -> float:
    m = 12 if len(train) > 24 else 1
    diffs = np.abs(train[m:] - train[:-m])
    scale = float(np.mean(diffs)) if len(diffs) else 0.0
    return scale if scale > 1e-9 else float(np.mean(np.abs(train))) or 1.0


def pinball(y: np.ndarray, q: np.ndarray, tau: float) -> float:
    d = y - q
    return float(np.mean(np.maximum(tau * d, (tau - 1) * d)))


def run(path: Path, holdout: int, seed: int) -> pd.DataFrame:
    engine = create_engine("sqlite:///:memory:")
    import app.models  # noqa: F401 -- register mappers

    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    cal = get_calendar_config()
    series = load_series(path)

    items: dict[str, LineItem] = {}
    for i, (code, (name, _)) in enumerate(series.items(), start=1):
        li = LineItem(id=i, account_code=code, name=name, category="Revenue", allow_negative=False)
        db.add(li)
        items[code] = li
    db.commit()

    base_analysis = resolve_analysis_settings(db, business_unit_id=None)
    results = []
    for config, toggles in CONFIGS.items():
        analysis = dataclasses.replace(base_analysis, **toggles)
        registry = registry_for_analysis(analysis)
        train_by_line, periods_by_line, dates_by_line = {}, {}, {}
        for code, (_, g) in series.items():
            tr = g.iloc[:-holdout]
            li = items[code]
            train_by_line[li.id] = pd.Series(tr["value"].astype(float).values)
            periods_by_line[li.id] = list(tr["period"])
            dates_by_line[li.id] = pd.DatetimeIndex(
                [pd.Timestamp(period_to_date(p, cal)) for p in periods_by_line[li.id]]
            )

        panel = None
        t0 = time.time()
        if analysis.enable_global_gbm_model:
            effective = {
                li.id: compute_effective_series(
                    li, train_by_line[li.id], dates_by_line[li.id], analysis
                )
                for li in items.values()
            }
            panel = build_global_panel_context(
                db,
                line_items=list(items.values()),
                effective_series_by_line=effective,
                periods_by_line=periods_by_line,
                cal_cfg=cal,
                version_id=f"backtest-{config}",
                horizon=holdout,
                random_seed=seed,
                registry=registry,
            )

        for code, (_, g) in series.items():
            li = items[code]
            ctx = LineForecastContext(
                version_id=f"backtest-{config}",
                horizon=holdout,
                random_seed=seed,
                model_type="auto",
                is_material=True,
                cal_cfg=cal,
                model_registry=registry,
                analysis=analysis,
                panel=panel,
            )
            out = forecast_line_item(
                None, li, train_by_line[li.id], dates_by_line[li.id], periods_by_line[li.id], ctx  # type: ignore[arg-type]
            )
            actual = g.iloc[-holdout:]["value"].astype(float).values
            rows = sorted(out.line_rows, key=lambda r: r["period"])[:holdout]
            if out.skipped or len(rows) < holdout:
                continue
            p50 = np.array([r["p50"] for r in rows], dtype=float)
            p10 = np.array([r.get("p10", r["p50"]) for r in rows], dtype=float)
            p90 = np.array([r.get("p90", r["p50"]) for r in rows], dtype=float)
            scale = naive_scale(train_by_line[li.id].values)
            results.append({
                "config": config,
                "series": code,
                "model": out.selected_model,
                "mase": float(np.mean(np.abs(actual - p50))) / scale,
                "pinball": (pinball(actual, p10, 0.1) + pinball(actual, p90, 0.9)) / 2 / scale,
                "coverage80": float(np.mean((actual >= p10) & (actual <= p90))),
            })
        print(f"  {config}: {time.time() - t0:.1f}s", file=sys.stderr)
    db.close()
    return pd.DataFrame(results)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("csv", nargs="+", type=Path)
    parser.add_argument("--holdout", type=int, default=6)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--group-part", type=int, default=None,
        help="Also break results down by this '-'-separated part of the account code "
        "(e.g. 1 for SYN-<shape>-<n> from synthetic_panel.py)",
    )
    args = parser.parse_args()

    frames = []
    for path in args.csv:
        print(f"{path.name}:", file=sys.stderr)
        df = run(path, args.holdout, args.seed)
        df["dataset"] = path.name
        frames.append(df)
    df = pd.concat(frames)
    summary = df.groupby("config").agg(
        series=("series", "count"),
        mean_mase=("mase", "mean"),
        median_mase=("mase", "median"),
        mean_pinball=("pinball", "mean"),
        coverage80=("coverage80", "mean"),
    ).reindex(list(CONFIGS))
    base = summary.loc["baseline"]
    summary["mase_vs_base"] = summary["mean_mase"] / base["mean_mase"] - 1
    summary["pinball_vs_base"] = summary["mean_pinball"] / base["mean_pinball"] - 1
    summary["ships"] = (summary["mase_vs_base"] < 0) & (summary["pinball_vs_base"] <= 0)
    pd.set_option("display.width", 160)
    print(summary.round(4).to_string())
    if args.group_part is not None:
        df["group"] = df["series"].str.split("-").str[args.group_part]
        by = df.pivot_table(index="group", columns="config", values=["mase", "pinball"], aggfunc="mean")
        print("\nby group (mean scaled MASE / pinball):")
        print(by.round(3).to_string())
    print("\nselected models (+blend+gbm):")
    print(df[df.config == "+blend+gbm"]["model"].value_counts().to_string())


if __name__ == "__main__":
    main()
