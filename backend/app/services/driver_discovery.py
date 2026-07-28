"""Phase 9 — statistical driver discovery (MVP).

Scans existing :class:`Driver` series against a line item's actuals over a small
lag grid, then applies honesty gates before writing **candidate** links. Nothing
here can produce an ``active`` link: promotion stays a human decision through
``promote_link``.

Gates, in order:
  1. Overlap floor (``min_overlap``, default 18 periods).
  2. Benjamini–Hochberg adjusted p-value across the whole (driver × lag) family.
  3. Minimum absolute elasticity, when elasticity is computable.
  4. Sign prior for the (driver_type, line family) pair — wrong sign is rejected.
  5. First-difference corroboration: the same relation must hold on Δy vs Δx with
     the same sign. Two independent random walks correlate in levels but not in
     differences, so this is what kills spurious regressions.
  6. Placebo. For a driver that looks integrated we resample its *increments*
     and re-cumulate, which keeps the drift and integration order under the null;
     otherwise we use a circular block bootstrap that keeps autocorrelation.

Sign priors live in the admin-editable ``sign_priors`` table and fall back to
``DEFAULT_SIGN_PRIORS`` for any (driver_type, line family) pair the table does
not cover, so an empty table behaves exactly like the hardcoded defaults.
"""

from __future__ import annotations

import logging
import math
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from app.models.actuals import ActualsRecord
from app.models.driver import Driver, DriverDiscoveryRun, DriverLink
from app.models.line_item import LineItem
from app.models.user import User
from app.services.audit import record_audit
from app.services.driver_series import materialize_driver_series
from app.services.outlier_cleaning import winsorize_outliers
from app.services.permissions import scoped_drivers

logger = logging.getLogger(__name__)

# Hard floor on overlapping observations — below this we do not test at all.
MIN_OVERLAP_FLOOR = 18

# Line-category → economic family used for sign priors.
_REVENUE_CATEGORIES = frozenset({"revenue", "sales", "income", "net revenue"})
_EXPENSE_CATEGORIES = frozenset(
    {
        "cogs",
        "cost of goods sold",
        "opex",
        "expense",
        "expenses",
        "operating expenses",
        "sg&a",
        "payroll",
        "people cost",
    }
)

# (driver_type, line_family) → expected sign of the level coefficient.
# Assumes expense lines are stored magnitude-positive (the repo convention:
# LineItem.sign_convention carries the display sign, not the stored sign).
DEFAULT_SIGN_PRIORS: dict[tuple[str, str], int] = {
    ("headcount", "expense"): 1,
    ("headcount", "revenue"): 1,
    ("volume", "revenue"): 1,
    ("volume", "expense"): 1,
    ("price", "revenue"): 1,
    ("rate", "expense"): 1,
}


@dataclass
class DiscoveryConfig:
    """Tunable knobs. Serialized verbatim into ``DriverDiscoveryRun.config``."""

    min_overlap: int = MIN_OVERLAP_FLOOR
    max_lag: int | None = None  # None → min(6, n // 6)
    alpha: float = 0.05
    min_abs_elasticity: float = 0.05
    max_survivors: int = 3
    max_candidates: int = 50
    require_difference_corroboration: bool = True
    difference_alpha: float = 0.05
    enable_placebo: bool = True
    placebo_draws: int = 200
    clean_series: bool = True
    driver_ids: list[int] | None = None
    random_seed: int = 42

    def resolved_min_overlap(self) -> int:
        return max(MIN_OVERLAP_FLOOR, int(self.min_overlap))


@dataclass
class CandidateFit:
    """One (driver, lag) test with its statistics and gate verdict."""

    driver_id: int
    driver_key: str
    driver_type: str
    lag: int
    n_obs: int
    coefficient: float
    coefficient_se: float
    t_stat: float
    p_value: float
    r2: float
    fit_method: str
    hac_lags: int | None
    elasticity: float | None
    expected_sign: int | None
    diff_coefficient: float | None = None
    diff_p_value: float | None = None
    diff_p_value_adj: float | None = None
    p_value_adj: float | None = None
    placebo_p: float | None = None
    placebo_method: str | None = None
    passed: bool = False
    reject_reason: str | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "driver_id": self.driver_id,
            "driver_key": self.driver_key,
            "driver_type": self.driver_type,
            "lag": self.lag,
            "n_obs": self.n_obs,
            "coefficient": _round(self.coefficient),
            "coefficient_se": _round(self.coefficient_se),
            "t_stat": _round(self.t_stat),
            "p_value": _round(self.p_value, 6),
            "p_value_adj": _round(self.p_value_adj, 6),
            "r2": _round(self.r2, 4),
            "elasticity": _round(self.elasticity, 4),
            "expected_sign": self.expected_sign,
            "diff_coefficient": _round(self.diff_coefficient),
            "diff_p_value": _round(self.diff_p_value, 6),
            "diff_p_value_adj": _round(self.diff_p_value_adj, 6),
            "placebo_p": _round(self.placebo_p, 4),
            "placebo_method": self.placebo_method,
            "fit_method": self.fit_method,
            "hac_lags": self.hac_lags,
            "passed": self.passed,
            "reject_reason": self.reject_reason,
        }


def _round(v: float | None, ndigits: int = 6) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f):
        return None
    return round(f, ndigits)


def line_family(line: LineItem) -> str | None:
    """Map a line item's category to the sign-prior family, if recognizable."""
    cat = (line.category or "").strip().lower()
    if cat in _REVENUE_CATEGORIES:
        return "revenue"
    if cat in _EXPENSE_CATEGORIES:
        return "expense"
    return None


def resolve_sign_prior(
    driver_type: str, family: str | None, db: Session | None = None
) -> tuple[int | None, str]:
    """Return ``(expected_sign, source)`` for one (driver_type, family) pair.

    ``source`` is one of ``sign_priors_table``, ``default_dict`` or ``none`` so
    the run summary can say where a rejection's prior came from.
    """
    if family is None:
        return None, "none"
    key = ((driver_type or "").strip().lower(), family)
    if db is not None:
        try:
            from app.models.sign_prior import SignPrior

            row = (
                db.query(SignPrior)
                .filter(
                    SignPrior.driver_type == key[0],
                    SignPrior.line_family == key[1],
                )
                .first()
            )
        except Exception as e:  # pragma: no cover — schema predating migration 018
            logger.debug("sign_priors lookup failed for %s: %s", key, e)
            row = None
        if row is not None and int(row.expected_sign) in (-1, 1):
            return int(row.expected_sign), "sign_priors_table"
    default = DEFAULT_SIGN_PRIORS.get(key)
    return default, "default_dict" if default is not None else "none"


def expected_sign_for(
    driver_type: str, family: str | None, db: Session | None = None
) -> int | None:
    """Prior on the coefficient sign; ``None`` means we hold no opinion.

    Reads the ``sign_priors`` table when a session is supplied, falling back to
    ``DEFAULT_SIGN_PRIORS``.
    """
    return resolve_sign_prior(driver_type, family, db)[0]


def line_actuals_series(db: Session, line_item_id: int) -> pd.Series:
    """Period-indexed actuals for a line item, summed across datasets."""
    rows = (
        db.query(ActualsRecord.period, ActualsRecord.value)
        .filter(ActualsRecord.line_item_id == line_item_id)
        .order_by(ActualsRecord.period)
        .all()
    )
    if not rows:
        return pd.Series(dtype=float)
    agg: dict[str, float] = {}
    for period, value in rows:
        agg[period] = agg.get(period, 0.0) + float(value)
    return pd.Series(agg, dtype=float).sort_index()


def _clean(series: pd.Series, *, enabled: bool) -> tuple[pd.Series, dict[str, Any]]:
    """Winsorize spikes at the MAD fence; never drops periods."""
    s = series.dropna().astype(float).sort_index()
    if not enabled or len(s) < 6:
        return s, {"method": "disabled" if not enabled else "skipped_short", "n_cleaned": 0}
    try:
        dates = pd.DatetimeIndex([pd.Timestamp(f"{p}-01") for p in s.index])
    except Exception:
        # Fiscal labels (FY2026-P01) are not parseable — skip rather than guess.
        return s, {"method": "skipped_unparseable_periods", "n_cleaned": 0}
    try:
        res = winsorize_outliers(s, dates)
    except Exception as e:  # pragma: no cover — defensive
        logger.debug("winsorize failed: %s", e)
        return s, {"method": "failed", "n_cleaned": 0}
    cleaned = res.cleaned
    cleaned.index = s.index
    return cleaned, {
        "method": res.method,
        "n_cleaned": res.n_cleaned,
        "periods": res.cleaned_periods,
    }


def _lagged_pair(
    y: pd.Series, x: pd.Series, lag: int
) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """Align ``driver(t-lag)`` with ``line(t)`` — same convention as build_exog_for_line."""
    periods = sorted(set(map(str, y.index)) | set(map(str, x.index)))
    idx = pd.Index(periods, dtype=str)
    shifted = x.reindex(idx).shift(int(lag))
    frame = pd.DataFrame({"y": y.reindex(idx), "x": shifted}).dropna()
    return (
        frame["y"].to_numpy(dtype=float),
        frame["x"].to_numpy(dtype=float),
        [str(p) for p in frame.index],
    )


def _two_sided_p(t_stat: float, dof: int) -> float:
    if dof <= 0 or not math.isfinite(t_stat):
        return 1.0
    try:
        from scipy import stats

        return float(2.0 * stats.t.sf(abs(t_stat), dof))
    except Exception:
        # Normal approximation fallback when scipy is unavailable.
        return float(math.erfc(abs(t_stat) / math.sqrt(2.0)))


def _ols_numpy(y: np.ndarray, x: np.ndarray) -> dict[str, float] | None:
    """Plain OLS of y on [1, x]. ``None`` when the design is degenerate."""
    n = len(y)
    if n < 3 or float(np.std(x)) < 1e-12:
        return None
    design = np.column_stack([np.ones(n), x])
    try:
        beta, *_ = np.linalg.lstsq(design, y, rcond=None)
        resid = y - design @ beta
        dof = n - 2
        sigma2 = float(resid @ resid) / dof
        xtx_inv = np.linalg.inv(design.T @ design)
    except np.linalg.LinAlgError:
        return None
    var_b = sigma2 * float(xtx_inv[1, 1])
    if not math.isfinite(var_b) or var_b <= 0:
        return None
    se = math.sqrt(var_b)
    t_stat = float(beta[1]) / se
    tss = float(((y - y.mean()) ** 2).sum())
    rss = float(resid @ resid)
    r2 = 1.0 - rss / tss if tss > 1e-12 else 0.0
    return {
        "coefficient": float(beta[1]),
        "coefficient_se": se,
        "t_stat": t_stat,
        "p_value": _two_sided_p(t_stat, dof),
        "r2": r2,
    }


def _hac_maxlags(n: int) -> int:
    """Newey–West rule of thumb, floored at 1."""
    return max(1, int(math.floor(4.0 * (n / 100.0) ** (2.0 / 9.0))))


def _ols_fit(y: np.ndarray, x: np.ndarray) -> dict[str, Any] | None:
    """OLS with HAC (Newey–West) standard errors when statsmodels is available."""
    baseline = _ols_numpy(y, x)
    if baseline is None:
        return None
    n = len(y)
    lags = _hac_maxlags(n)
    try:
        import statsmodels.api as sm

        res = sm.OLS(y, sm.add_constant(x)).fit(
            cov_type="HAC", cov_kwds={"maxlags": lags}
        )
        se = float(res.bse[1])
        if math.isfinite(se) and se > 0:
            return {
                "coefficient": float(res.params[1]),
                "coefficient_se": se,
                "t_stat": float(res.tvalues[1]),
                "p_value": float(res.pvalues[1]),
                "r2": float(res.rsquared),
                "fit_method": "ols_hac",
                "hac_lags": lags,
            }
    except Exception as e:
        logger.debug("HAC fit unavailable, falling back to OLS: %s", e)
    return {**baseline, "fit_method": "ols", "hac_lags": None}


def _benjamini_hochberg(p_values: list[float]) -> list[float]:
    """Step-up BH adjusted p-values, monotone and clipped to 1."""
    m = len(p_values)
    if m == 0:
        return []
    order = sorted(range(m), key=lambda i: p_values[i])
    adjusted = [1.0] * m
    running = 1.0
    for rank, i in enumerate(reversed(order), start=1):
        k = m - rank + 1  # 1-based rank of p_values[i]
        running = min(running, p_values[i] * m / k)
        adjusted[i] = min(1.0, max(0.0, running))
    return adjusted


def _elasticity(y: np.ndarray, x: np.ndarray, coefficient: float) -> float | None:
    """β · x̄/ȳ — undefined when either mean sits at zero."""
    y_bar = float(np.mean(y))
    x_bar = float(np.mean(x))
    if abs(y_bar) < 1e-9 or abs(x_bar) < 1e-9:
        return None
    val = coefficient * x_bar / y_bar
    return val if math.isfinite(val) else None


def _difference_fit(y: np.ndarray, x: np.ndarray) -> dict[str, float] | None:
    """OLS on first differences.

    Assumes the aligned periods are contiguous, which holds for the monthly
    actuals this runs on; a gap simply makes one increment span two periods.
    """
    if len(y) < 4:
        return None
    return _ols_numpy(np.diff(y), np.diff(x))


def _looks_integrated(x: np.ndarray) -> bool:
    """Cheap ADF screen (constant + trend). True when we cannot reject a unit root."""
    if len(x) < 12:
        return False
    try:
        from statsmodels.tsa.stattools import adfuller

        p_value = float(adfuller(x, regression="ct", autolag="AIC")[1])
    except Exception as e:
        logger.debug("ADF screen unavailable: %s", e)
        return False
    return p_value > 0.10


def _null_draw_block(x: np.ndarray, rng: np.random.Generator, block: int) -> np.ndarray:
    """Circular block bootstrap — keeps short-run autocorrelation, breaks the trend."""
    n = len(x)
    n_blocks = int(math.ceil(n / block))
    offsets = np.arange(block)
    starts = rng.integers(0, n, size=n_blocks)
    idx = np.concatenate([(start + offsets) % n for start in starts])[:n]
    return x[idx]


def _null_draw_increments(x: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Permute increments and re-cumulate — keeps drift and integration order.

    This is the null that matters for trending/integrated drivers: without it a
    spurious level regression between two random walks looks decisive.
    """
    increments = rng.permutation(np.diff(x))
    return np.concatenate([x[:1], x[0] + np.cumsum(increments)])


def _difference_corroborates(fit: CandidateFit, alpha: float) -> bool:
    """Δy vs Δx must clear FDR at ``alpha`` and agree in sign with the level fit."""
    if fit.diff_p_value_adj is None or fit.diff_coefficient is None:
        return False
    if fit.diff_p_value_adj >= alpha:
        return False
    return math.copysign(1.0, fit.diff_coefficient) == math.copysign(1.0, fit.coefficient)


def _placebo_p_value(
    y: np.ndarray,
    x: np.ndarray,
    t_obs: float,
    *,
    draws: int,
    rng: np.random.Generator,
) -> tuple[float | None, str]:
    """Share of null draws whose |t| matches or beats the observed one."""
    n = len(x)
    integrated = _looks_integrated(x)
    method = "increment_bootstrap" if integrated else "block_bootstrap"
    block = max(2, int(round(math.sqrt(n))))
    hits = 0
    valid = 0
    for _ in range(max(1, draws)):
        x_null = (
            _null_draw_increments(x, rng)
            if integrated
            else _null_draw_block(x, rng, block)
        )
        fit = _ols_numpy(y, x_null)
        if fit is None:
            continue
        valid += 1
        if abs(fit["t_stat"]) >= abs(t_obs):
            hits += 1
    if valid == 0:
        return None, method
    return (1.0 + hits) / (valid + 1.0), method


def _candidate_drivers(
    db: Session,
    line: LineItem,
    config: DiscoveryConfig,
    actor: User | None,
) -> tuple[list[Driver], str]:
    """BU-compatible drivers when any exist, else all in-scope drivers."""
    base = scoped_drivers(db, actor).order_by(Driver.id.asc())
    if config.driver_ids:
        wanted = [int(i) for i in config.driver_ids]
        return base.filter(Driver.id.in_(wanted)).all(), "explicit_ids"

    if line.business_unit:
        bu_matched = base.filter(
            (Driver.business_unit == line.business_unit) | (Driver.business_unit.is_(None))
        ).all()
        if bu_matched:
            return bu_matched[: config.max_candidates], "bu_compatible"
    return base.limit(config.max_candidates).all(), "all_in_scope"


def _supersede_prior_candidates(db: Session, *, line_item_id: int, run_id: str) -> int:
    """Retire candidates from earlier discovery runs for this line.

    Manually asserted links are untouched — only rows carrying a discovery_run_id.
    """
    stale = (
        db.query(DriverLink)
        .filter(
            DriverLink.line_item_id == line_item_id,
            DriverLink.status == "candidate",
            DriverLink.discovery_run_id.isnot(None),
            DriverLink.discovery_run_id != run_id,
        )
        .all()
    )
    for link in stale:
        link.status = "superseded"
    if stale:
        db.flush()
    return len(stale)


def discover_drivers_for_line(
    db: Session,
    line_item_id: int,
    *,
    actor: User | None = None,
    config: DiscoveryConfig | dict[str, Any] | None = None,
) -> DriverDiscoveryRun:
    """Scan drivers for a line item and persist a discovery run + candidate links.

    Always returns a persisted (flushed) ``DriverDiscoveryRun``; the caller owns
    the commit. Never creates ``active`` links.
    """
    if isinstance(config, dict):
        known = {f for f in DiscoveryConfig.__dataclass_fields__}
        cfg = DiscoveryConfig(**{k: v for k, v in config.items() if k in known and v is not None})
    else:
        cfg = config or DiscoveryConfig()

    line = db.query(LineItem).filter(LineItem.id == line_item_id).first()
    if line is None:
        raise ValueError(f"Line item {line_item_id} not found")

    run = DriverDiscoveryRun(
        line_item_id=line_item_id,
        config=asdict(cfg),
        status="running",
        created_by=actor.id if actor else None,
    )
    db.add(run)
    db.flush()

    min_overlap = cfg.resolved_min_overlap()
    raw_line = line_actuals_series(db, line_item_id)
    y_series, line_clean_meta = _clean(raw_line, enabled=cfg.clean_series)

    def _finish(status: str, summary: dict[str, Any]) -> DriverDiscoveryRun:
        run.status = status
        run.summary = summary
        db.flush()
        if actor is not None:
            record_audit(
                db,
                action="driver.discovery.run",
                entity_type="driver_discovery_run",
                entity_id=str(run.id),
                actor_id=actor.id,
                actor_username=actor.username,
                details={
                    "line_item_id": line_item_id,
                    "status": status,
                    "n_tests": summary.get("n_tests", 0),
                    "n_survivors": summary.get("n_survivors", 0),
                    "superseded": summary.get("superseded_links", 0),
                },
            )
        return run

    if len(y_series) < min_overlap:
        return _finish(
            "insufficient_data",
            {
                "reason": "line_series_too_short",
                "n_line_periods": int(len(y_series)),
                "min_overlap": min_overlap,
                "n_tests": 0,
                "n_survivors": 0,
                "candidates": [],
            },
        )

    family = line_family(line)
    max_lag = (
        int(cfg.max_lag)
        if cfg.max_lag is not None
        else min(6, len(y_series) // 6)
    )
    max_lag = max(0, max_lag)

    drivers, selection_mode = _candidate_drivers(db, line, cfg, actor)
    fits: list[CandidateFit] = []
    skipped: list[dict[str, Any]] = []
    cleaned_drivers: dict[int, pd.Series] = {}

    for driver in drivers:
        raw_x = materialize_driver_series(db, driver_id=driver.id, value_type="actual")
        if raw_x.empty:
            skipped.append({"driver_id": driver.id, "driver_key": driver.key, "reason": "no_values"})
            continue
        x_series, x_clean_meta = _clean(raw_x, enabled=cfg.clean_series)
        cleaned_drivers[driver.id] = x_series
        expected, prior_source = resolve_sign_prior(driver.driver_type, family, db)

        for lag in range(0, max_lag + 1):
            y_arr, x_arr, periods = _lagged_pair(y_series, x_series, lag)
            if len(y_arr) < min_overlap:
                skipped.append(
                    {
                        "driver_id": driver.id,
                        "driver_key": driver.key,
                        "lag": lag,
                        "reason": "insufficient_overlap",
                        "n_obs": int(len(y_arr)),
                    }
                )
                continue
            fit = _ols_fit(y_arr, x_arr)
            if fit is None:
                skipped.append(
                    {
                        "driver_id": driver.id,
                        "driver_key": driver.key,
                        "lag": lag,
                        "reason": "degenerate_design",
                    }
                )
                continue
            diff_fit = _difference_fit(y_arr, x_arr)
            fits.append(
                CandidateFit(
                    driver_id=driver.id,
                    driver_key=driver.key,
                    driver_type=driver.driver_type,
                    lag=lag,
                    n_obs=len(y_arr),
                    coefficient=fit["coefficient"],
                    coefficient_se=fit["coefficient_se"],
                    t_stat=fit["t_stat"],
                    p_value=fit["p_value"],
                    r2=fit["r2"],
                    fit_method=fit["fit_method"],
                    hac_lags=fit["hac_lags"],
                    elasticity=_elasticity(y_arr, x_arr, fit["coefficient"]),
                    expected_sign=expected,
                    diff_coefficient=diff_fit["coefficient"] if diff_fit else None,
                    diff_p_value=diff_fit["p_value"] if diff_fit else None,
                    diagnostics={
                        "period_from": periods[0],
                        "period_to": periods[-1],
                        "line_cleaning": line_clean_meta,
                        "driver_cleaning": x_clean_meta,
                        "line_family": family,
                        "sign_prior_source": prior_source,
                    },
                )
            )

    if not fits:
        return _finish(
            "insufficient_data",
            {
                "reason": "no_testable_candidates",
                "n_drivers_considered": len(drivers),
                "driver_selection": selection_mode,
                "min_overlap": min_overlap,
                "max_lag": max_lag,
                "n_tests": 0,
                "n_survivors": 0,
                "candidates": [],
                "skipped": skipped[:50],
            },
        )

    # One family for both the level and the difference test — every test we ran
    # carries multiplicity, so both get BH-adjusted over the same family size.
    family_size = len(fits)
    level_adj = _benjamini_hochberg([f.p_value for f in fits])
    diff_adj = _benjamini_hochberg(
        [f.diff_p_value if f.diff_p_value is not None else 1.0 for f in fits]
    )
    for fit, adj, d_adj in zip(fits, level_adj, diff_adj):
        fit.p_value_adj = adj
        fit.diff_p_value_adj = d_adj if fit.diff_p_value is not None else None
        fit.diagnostics["family_size"] = family_size

    # Statistical + economic gates before the (costlier) placebo.
    rng = np.random.default_rng(cfg.random_seed)
    for fit in fits:
        if fit.p_value_adj is None or fit.p_value_adj >= cfg.alpha:
            fit.reject_reason = "fdr_not_significant"
            continue
        if fit.elasticity is not None and abs(fit.elasticity) < cfg.min_abs_elasticity:
            fit.reject_reason = "elasticity_below_floor"
            continue
        if fit.expected_sign is not None and (
            math.copysign(1.0, fit.coefficient) != float(fit.expected_sign)
        ):
            fit.reject_reason = "sign_prior_violated"
            continue
        if cfg.require_difference_corroboration and not _difference_corroborates(
            fit, cfg.difference_alpha
        ):
            fit.reject_reason = "difference_not_corroborated"
            continue
        fit.passed = True

    # Keep only the best lag per driver, then placebo-test the shortlist.
    best_by_driver: dict[int, CandidateFit] = {}
    for fit in sorted(
        (f for f in fits if f.passed),
        key=lambda f: (f.p_value_adj if f.p_value_adj is not None else 1.0, -abs(f.t_stat)),
    ):
        if fit.driver_id in best_by_driver:
            fit.passed = False
            fit.reject_reason = "dominated_by_better_lag"
            continue
        best_by_driver[fit.driver_id] = fit

    shortlist = sorted(
        best_by_driver.values(),
        key=lambda f: (f.p_value_adj if f.p_value_adj is not None else 1.0, -abs(f.t_stat)),
    )

    if cfg.enable_placebo:
        for fit in shortlist:
            y_arr, x_arr, _ = _lagged_pair(
                y_series, cleaned_drivers[fit.driver_id], fit.lag
            )
            placebo, method = _placebo_p_value(
                y_arr, x_arr, fit.t_stat, draws=cfg.placebo_draws, rng=rng
            )
            fit.placebo_p = placebo
            fit.placebo_method = method
            fit.diagnostics["placebo_draws"] = cfg.placebo_draws
            fit.diagnostics["placebo_method"] = method
            if placebo is not None and placebo >= cfg.alpha:
                fit.passed = False
                fit.reject_reason = "placebo_not_significant"

    survivors = [f for f in shortlist if f.passed][: cfg.max_survivors]
    kept = {id(f) for f in survivors}
    for fit in shortlist:
        if fit.passed and id(fit) not in kept:
            fit.passed = False
            fit.reject_reason = "below_survivor_cap"

    superseded = _supersede_prior_candidates(db, line_item_id=line_item_id, run_id=run.id)

    created: list[int] = []
    for fit in survivors:
        link = DriverLink(
            driver_id=fit.driver_id,
            line_item_id=line_item_id,
            link_type="discovered",
            relation="level",
            transform="level",
            lag=fit.lag,
            coefficient=fit.coefficient,
            coefficient_se=fit.coefficient_se,
            elasticity=fit.elasticity,
            t_stat=fit.t_stat,
            p_value=fit.p_value,
            p_value_adj=fit.p_value_adj,
            r2=fit.r2,
            n_obs=fit.n_obs,
            fit_method=fit.fit_method,
            hac_lags=fit.hac_lags,
            diagnostics={
                **fit.diagnostics,
                "placebo_p": _round(fit.placebo_p, 4),
                "diff_coefficient": _round(fit.diff_coefficient),
                "diff_p_value": _round(fit.diff_p_value, 6),
                "diff_p_value_adj": _round(fit.diff_p_value_adj, 6),
                "alpha": cfg.alpha,
                "expected_sign": fit.expected_sign,
            },
            discovery_run_id=run.id,
            # Discovery never activates a link — promotion is a human decision.
            status="candidate",
            notes=(
                f"Discovered lag={fit.lag} adj_p={_round(fit.p_value_adj, 4)} "
                f"n={fit.n_obs} via {fit.fit_method}"
            ),
            created_by=actor.id if actor else None,
        )
        db.add(link)
        db.flush()
        created.append(link.id)

    ranked = sorted(
        fits,
        key=lambda f: (
            0 if f.passed else 1,
            f.p_value_adj if f.p_value_adj is not None else 1.0,
        ),
    )
    return _finish(
        "completed",
        {
            "line_item_id": line_item_id,
            "line_account_code": line.account_code,
            "line_family": family,
            "n_line_periods": int(len(y_series)),
            "min_overlap": min_overlap,
            "max_lag": max_lag,
            "alpha": cfg.alpha,
            "family_size": family_size,
            "n_drivers_considered": len(drivers),
            "driver_selection": selection_mode,
            "n_tests": family_size,
            "n_survivors": len(survivors),
            "survivor_link_ids": created,
            "superseded_links": superseded,
            "placebo_enabled": cfg.enable_placebo,
            "difference_corroboration": cfg.require_difference_corroboration,
            "sign_priors": "sign_priors table with DEFAULT_SIGN_PRIORS fallback",
            "candidates": [f.as_dict() for f in ranked[:50]],
            "skipped": skipped[:50],
        },
    )


def discovery_run_dict(run: DriverDiscoveryRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "line_item_id": run.line_item_id,
        "config": run.config,
        "summary": run.summary,
        "status": run.status,
        "created_by": run.created_by,
        "created_at": run.created_at.isoformat() if run.created_at else None,
    }
