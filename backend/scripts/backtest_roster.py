"""Holdout backtest for the forecasting roster (enhancement plan, Phase 2).

The plan's ship criterion for new model behavior: the new roster must win on
MASE *and* not regress pinball loss. This runs the real per-line pipeline
(`forecast_line_item`: selection, blending, conformal calibration) on each
series with its last ``--holdout`` months withheld, under four settings, and
scores the withheld months:

    baseline      single CV winner per line (current defaults)
    +blend        enable_ensemble_blending
    +gbm          enable_global_gbm_model (panel built on the training window)
    +blend+gbm    both, without CV-coverage widening (production until
                  2026-11-26)
    +cvwiden      +blend+gbm, widening each band by its CV coverage
                  (cv_coverage_widening_enabled; the production default)
    +conformal    +cvwiden with split-conformal interval calibration
                  (conformal_calibration_enabled), the coverage report the
                  setting's comment asks for before turning it on
    +fm           +cvwiden plus Chronos-2 (enable_foundation_models), which
                  competes through the same selection, band and blending
    +range        +cvwiden, with each line's P10/P90 averaged with Chronos-2's
                  (foundation_range_blend_enabled); Chronos-2 is not a
                  selection candidate, so point forecasts are unchanged
    +range-nowiden  the same without CV-coverage widening
    +fm+range     +fm, with each non-Chronos-2 line's P10/P90 averaged with
                  Chronos-2's (foundation_range_blend_enabled)
    +fm+range-nowiden  the same without CV-coverage widening
    chronos2-raw  Chronos-2's own zero-shot P10/P50/P90 from the +fm run,
                  for every line, with no selection or calibration (the
                  "what if we just used the foundation model" arm)

The Chronos-2 arms need the fm-service sidecar (``--fm-url``, default
http://localhost:8100). With no drivers in these CSVs, Chronos-2 runs
univariate here; the panel still gives it group context across lines.

MASE and pinball are scaled by each series' in-sample naive MAE so they can be
averaged across series of very different sizes.

    ./venv/bin/python scripts/backtest_roster.py ../test_actuals_data.csv

By default every line is forecast from 4 origins, 3 months apart, and each
arm is compared with the default by a paired bootstrap over series (95% CI);
``--origins 1`` reproduces the single-holdout runs recorded for Phase D.
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
from app.config import settings  # noqa: E402
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
    "+cvwiden": {
        "enable_ensemble_blending": True,
        "enable_global_gbm_model": True,
        "cv_coverage_widening_enabled": True,
    },
    "+conformal": {
        "enable_ensemble_blending": True,
        "enable_global_gbm_model": True,
        "cv_coverage_widening_enabled": True,
        "conformal_calibration_enabled": True,
    },
    "+fm": {
        "enable_ensemble_blending": True,
        "enable_global_gbm_model": True,
        "cv_coverage_widening_enabled": True,
        "enable_foundation_models": True,
    },
    "+range": {
        "enable_ensemble_blending": True,
        "enable_global_gbm_model": True,
        "cv_coverage_widening_enabled": True,
        "foundation_range_blend_enabled": True,
    },
    "+range-nowiden": {
        "enable_ensemble_blending": True,
        "enable_global_gbm_model": True,
        "foundation_range_blend_enabled": True,
    },
    "+fm+range": {
        "enable_ensemble_blending": True,
        "enable_global_gbm_model": True,
        "cv_coverage_widening_enabled": True,
        "enable_foundation_models": True,
        "foundation_range_blend_enabled": True,
    },
    "+fm+range-nowiden": {
        "enable_ensemble_blending": True,
        "enable_global_gbm_model": True,
        "enable_foundation_models": True,
        "foundation_range_blend_enabled": True,
    },
}
DEFAULT = "+cvwiden"
RAW = "chronos2-raw"
ORDER = [*CONFIGS, RAW]


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


def _score(actual, p10, p50, p90, scale) -> dict:
    return {
        "mase": float(np.mean(np.abs(actual - p50))) / scale,
        "pinball": (pinball(actual, p10, 0.1) + pinball(actual, p90, 0.9)) / 2 / scale,
        "coverage80": float(np.mean((actual >= p10) & (actual <= p90))),
        # Where the misses fall: both ~0.10 for a calibrated band.
        "below_p10": float(np.mean(actual < p10)),
        "above_p90": float(np.mean(actual > p90)),
        # Signed mean error over the scale (+ = forecast too low).
        "bias": float(np.mean(actual - p50)) / scale,
        "width": float(np.mean(p90 - p10)) / scale,
    }


def run(
    path: Path,
    holdout: int,
    seed: int,
    configs: dict,
    *,
    origins: int = 1,
    origin_step: int = 3,
    min_train: int = 8,
) -> pd.DataFrame:
    """Score every config from ``origins`` forecast origins per line.

    Origin k withholds ``holdout + k * origin_step`` points from the end and
    scores the ``holdout`` points that follow the cut, so origins step back in
    time and their test windows overlap when origin_step < holdout. A line is
    used at an origin only when at least ``min_train`` points remain.
    """
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

    analysis_fields = {f.name for f in dataclasses.fields(base_analysis)}
    for config, toggles in configs.items():
        # Toggles the AnalysisProfile does not carry are process settings.
        # Every arm's keys, not only this run's: a toggle no arm in this run
        # names would otherwise keep the process default.
        for key in {k for c in CONFIGS.values() for k in c} - analysis_fields:
            setattr(settings, key, bool(toggles.get(key, False)))
        analysis = dataclasses.replace(
            base_analysis, **{k: v for k, v in toggles.items() if k in analysis_fields}
        )
        registry = registry_for_analysis(analysis)
        t0 = time.time()
        for origin in range(origins):
            results.extend(_run_origin(
                db, series, items, cal, analysis, registry, config,
                holdout=holdout, seed=seed, cut=holdout + origin * origin_step,
                min_train=min_train, origin=origin,
            ))
        print(f"  {config}: {time.time() - t0:.1f}s", file=sys.stderr)
    db.close()
    return pd.DataFrame(results)


def _run_origin(
    db, series, items, cal, analysis, registry, config, *,
    holdout: int, seed: int, cut: int, min_train: int, origin: int,
) -> list[dict]:
    results: list[dict] = []
    train_by_line, periods_by_line, dates_by_line, actual_by_line = {}, {}, {}, {}
    for code, (_, g) in series.items():
        if len(g) - cut < min_train:
            continue
        tr = g.iloc[: len(g) - cut]
        li = items[code]
        train_by_line[li.id] = pd.Series(tr["value"].astype(float).values)
        periods_by_line[li.id] = list(tr["period"])
        dates_by_line[li.id] = pd.DatetimeIndex(
            [pd.Timestamp(period_to_date(p, cal)) for p in periods_by_line[li.id]]
        )
        actual_by_line[li.id] = g.iloc[len(g) - cut : len(g) - cut + holdout]["value"].astype(float).values
    used = [items[c] for c in series if items[c].id in train_by_line]

    panel = None
    if (
        analysis.enable_global_gbm_model
        or analysis.enable_foundation_models
        or settings.foundation_range_blend_enabled
    ):
        effective = {
            li.id: compute_effective_series(
                li, train_by_line[li.id], dates_by_line[li.id], analysis
            )
            for li in used
        }
        panel = build_global_panel_context(
            db,
            line_items=used,
            effective_series_by_line=effective,
            periods_by_line=periods_by_line,
            cal_cfg=cal,
            version_id=f"backtest-{config}-{origin}",
            horizon=holdout,
            random_seed=seed,
            registry=registry,
        )

    for li in used:
        code = li.account_code
        ctx = LineForecastContext(
            version_id=f"backtest-{config}-{origin}",
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
        actual = actual_by_line[li.id]
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
            "origin": origin,
            "model": out.selected_model,
            **_score(actual, p10, p50, p90, scale),
        })
        fm = getattr(panel, "foundation", None) if panel is not None else None
        if fm is not None and fm.status == "ok":
            raw = fm.forecasts.get((li.id, periods_by_line[li.id][-1]))
            if raw is not None and len(raw.p50) >= holdout:
                results.append({
                    "config": RAW,
                    "series": code,
                    "origin": origin,
                    "model": "chronos2",
                    **_score(actual, raw.p10[:holdout], raw.p50[:holdout], raw.p90[:holdout], scale),
                })
    if panel is not None and getattr(panel, "foundation", None) is not None:
        fm = panel.foundation
        print(f"    chronos2 (origin {origin}): {fm.status} {fm.skip_reason or ''}", file=sys.stderr)
    return results


def paired_change(
    df: pd.DataFrame, arm: str, metric: str, *, base: str = DEFAULT, n_boot: int = 2000, seed: int = 0
) -> tuple[float, float, float, int]:
    """Relative change of ``metric`` (arm vs base) with a 95% bootstrap CI.

    The unit is the series, not the (series, origin) row: one line's origins
    share most of their history, so resampling rows would overstate the
    evidence. Each series' metric is averaged over the origins both arms
    scored, then series are resampled with replacement. Returns
    (change, ci_low, ci_high, n_series).
    """
    key = ["dataset", "series", "origin"] if "dataset" in df.columns else ["series", "origin"]
    a = df[df.config == arm].set_index(key)[metric]
    b = df[df.config == base].set_index(key)[metric]
    joined = pd.concat([a.rename("a"), b.rename("b")], axis=1, join="inner").dropna()
    per_series = joined.groupby(level=list(range(len(key) - 1))).mean()
    n = len(per_series)
    if n == 0 or per_series["b"].mean() == 0:
        return float("nan"), float("nan"), float("nan"), n
    point = per_series["a"].mean() / per_series["b"].mean() - 1
    rng = np.random.default_rng(seed)
    av, bv = per_series["a"].to_numpy(), per_series["b"].to_numpy()
    idx = rng.integers(0, n, size=(n_boot, n))
    ratios = av[idx].mean(axis=1) / np.maximum(bv[idx].mean(axis=1), 1e-12) - 1
    lo, hi = np.percentile(ratios, [2.5, 97.5])
    return float(point), float(lo), float(hi), n


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
    parser.add_argument("--fm-url", default="http://localhost:8100", help="fm-service base URL")
    parser.add_argument("--out", type=Path, default=None, help="also write per-series rows to this CSV")
    parser.add_argument("--no-fm", action="store_true", help="skip the Chronos-2 arms")
    parser.add_argument(
        "--origins", type=int, default=4,
        help="forecast origins per line, each --origin-step months earlier than the last",
    )
    parser.add_argument("--origin-step", type=int, default=3)
    parser.add_argument(
        "--configs", default=None,
        help="comma-separated subset of arms to run (the default arm is always included)",
    )
    args = parser.parse_args()


    settings.fm_service_url = args.fm_url
    settings.fm_service_timeout_seconds = 1800.0
    configs = {k: v for k, v in CONFIGS.items() if not (args.no_fm and v.get("enable_foundation_models"))}
    if args.configs:
        wanted = {c.strip() for c in args.configs.split(",")} | {DEFAULT}
        configs = {k: v for k, v in configs.items() if k in wanted}

    frames = []
    for path in args.csv:
        print(f"{path.name}:", file=sys.stderr)
        df = run(
            path, args.holdout, args.seed, configs,
            origins=args.origins, origin_step=args.origin_step,
        )
        df["dataset"] = path.name
        frames.append(df)
    df = pd.concat(frames)
    if args.out is not None:
        df.to_csv(args.out, index=False)
    summary = df.groupby("config").agg(
        rows=("series", "count"),
        series=("series", "nunique"),
        mean_mase=("mase", "mean"),
        mean_pinball=("pinball", "mean"),
        coverage80=("coverage80", "mean"),
    ).reindex([c for c in ORDER if c in set(df["config"])])
    for metric in ("mase", "pinball"):
        changes = {c: paired_change(df, c, metric) for c in summary.index}
        summary[f"{metric}_vs_default"] = [changes[c][0] for c in summary.index]
        summary[f"{metric}_ci"] = [
            f"[{changes[c][1]:+.1%}, {changes[c][2]:+.1%}]" for c in summary.index
        ]
        summary[f"_{metric}_lo"] = [changes[c][1] for c in summary.index]
        summary[f"_{metric}_hi"] = [changes[c][2] for c in summary.index]
    # Ship gate: a MASE win the paired 95% CI excludes zero for, pinball not
    # significantly worse, and 80% coverage no more than 2 points further from
    # target than the default's. Point estimates alone decided Phase D's
    # ±1-2% calls, which one holdout origin cannot resolve.
    # An arm that only changes bands (point forecasts identical, so the MASE
    # CI is exactly zero) cannot win on MASE; it ships when pinball is not
    # significantly worse and coverage moves closer to target.
    base_gap = abs(summary.loc[DEFAULT, "coverage80"] - 0.8)
    gap = (summary["coverage80"] - 0.8).abs()
    bands_only = (summary["_mase_lo"].abs() < 1e-12) & (summary["_mase_hi"].abs() < 1e-12)
    summary["ships"] = (
        (summary["_pinball_lo"] <= 0)
        & (
            ((summary["_mase_hi"] < 0) & (gap <= base_gap + 0.02))
            | (bands_only & (gap < base_gap - 0.01))
        )
    )
    summary.loc[DEFAULT, "ships"] = False
    summary = summary.drop(columns=[c for c in summary.columns if c.startswith("_")])
    pd.set_option("display.width", 160)
    print(summary.round(4).to_string())
    if args.group_part is not None:
        df["group"] = df["series"].str.split("-").str[args.group_part]
        by = df.pivot_table(index="group", columns="config", values=["mase", "pinball"], aggfunc="mean")
        print("\nby group (mean scaled MASE / pinball):")
        print(by.round(3).to_string())
    for cfg in (DEFAULT, "+fm"):
        if cfg in set(df["config"]):
            print(f"\nselected models ({cfg}):")
            print(df[df.config == cfg]["model"].value_counts().to_string())


if __name__ == "__main__":
    main()
