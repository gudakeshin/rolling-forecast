# Rolling Forecast — Enhancement Plan

## Context

Rolling Forecast is a ~50k LOC FP&A platform (FastAPI + LangGraph agent + React) that generates
statistical baseline forecasts, collects human overrides, and runs a review/approval workflow.

I audited the whole app. The headline finding is an **asymmetry**:

> The forecasting engine is research-grade. The analyst experience on top of it is thin,
> and a "rolling" forecast currently never rolls by itself.

**What is genuinely strong** (do not rebuild any of this):
- Model selection (`backend/app/domain/engines/model_registry.py:353`) — rolling-origin CV, MASE →
  pinball → complexity ranking, a **1-SE band with an Occam tie-break**, two-stage cost screening,
  wall-clock budget. Better than most commercial FP&A tools.
- Exogenous-driver admission (`backend/app/services/forecast_pipeline.py:117`) — head-to-head
  plain-vs-exog CV with an explicit winner's-curse guard, and origin-restricted driver forecasts
  (`driver_forecast_cache.py`) so backtests don't leak.
- Driver discovery (`backend/app/services/driver_discovery.py`) — HAC standard errors,
  Benjamini–Hochberg FDR, sign priors, first-difference corroboration, block/increment placebo
  bootstrap. Discovery can never auto-activate a link; a human promotes it.
- Full MinT reconciliation (`backend/app/services/reconciliation.py:408`) with shrunk leaf
  correlation and nearest-correlation projection.
- Variance attribution (`backend/app/services/variance_attribution.py`) — Q×P identity, three-way
  Volume/Mix/Price, FX decomposition, a dedicated reconciliation bucket, and an honest
  `unattributed` residual.

**The three gaps worth money**, in order:
1. **The accuracy loop is 90% built and never closed.** `evaluate_cv` computes per-fold residuals
   and empirical `coverage_80` (`backend/app/domain/engines/base_model.py:288-305`) and then throws
   them away. Every prediction interval in the product is analytic Gaussian/t. Coverage is
   *measured* in three places and *used* in none.
2. **All that rigor is invisible to the analyst.** The model comparison table, exog
   admit/reject reasons, driver t-stats, MinT deltas, cleaned outliers — all computed, all
   serialized, almost none of it reachable from the cell the analyst is looking at.
3. **The workspace fights the analyst.** One panel at a time, 40–55% of the screen, filters lost on
   close, no saved views, no frozen columns, no bulk edit outside one panel, no undo, zero keyboard
   shortcuts.

Scope per your direction: **forecast accuracy, analyst workflow/UI, and end-user CX**, optimized for
the **FP&A analyst**. Enterprise connectors / multi-tenancy are explicitly out of scope.

---

## Phase 1 — Close the calibration loop (highest value per unit of effort)

Turns intervals from "model-assumed" into "empirically calibrated", using data the app already
computes and discards. This is the single change that most increases analyst trust.

**1.1 Return fold residuals from CV**
- `backend/app/domain/engines/base_model.py:224` `evaluate_cv` — alongside the existing metrics,
  return `fold_residuals: list[list[float]]` indexed by horizon step (fold k already computes
  `actuals - predicted` at line 264-267; just keep it).
- Carry through `ModelComparisonResult` / `ModelSelectionResult`
  (`backend/app/domain/engines/model_registry.py:30,55`) the same way `fold_mapes` already is.

**1.2 Split-conformal interval calibration, per horizon**
- New `backend/app/services/interval_calibration.py`:
  - `conformal_quantiles(residuals_by_h, alpha=0.20) -> dict[int, tuple[float, float]]` — empirical
    10th/90th percentile of residuals at each horizon step, with a small-sample correction and a
    fallback to the pooled residual set when a horizon has <5 observations.
  - `apply_calibration(point, h, quantiles) -> (p10, p90)`.
- Wire into `backend/app/services/forecast_pipeline.py` where p10/p50/p90 are written
  (currently lines ~892-928), **after** the existing exog variance inflation
  (`_inflate_exog_intervals`, line 354) so driver uncertainty still compounds.
- Tag `ForecastLineResult.bounds_method` with `conformal` / `conformal+exog_inflated`. The column
  and its consumers already exist (`backend/app/models/forecast.py`, `reconciliation.py:718`).
- Keep the analytic interval as the fallback when a line has too little history to calibrate, and
  say so in the metadata rather than silently degrading.

**1.3 Feed realized vintages back**
- `ForecastAccuracyRecord.within_p10_p90` is already persisted per vintage
  (`backend/app/models/fx.py`, written by `backend/app/services/accuracy_snapshot.py`).
- Extend `AccuracySnapshotService` with `realized_coverage(line_item_id, horizon_bucket, n_versions)`
  and blend it with the CV-derived quantiles (CV for cold lines, realized for lines with ≥3 closed
  cycles). This is the loop the product has been one function away from since Phase 8.

**1.4 Propagate uncertainty through scenarios**
- `backend/app/services/scenario_what_if.py:186-188` currently **restores base intervals** on shocked
  rows — a what-if answer today carries the wrong uncertainty. Propagate
  `var_new = var_base + Σ(β² · Var(driver_shock))`, reusing the delta-method helper already written
  at `forecast_pipeline.py:354`.

**1.5 Surface it**
- New field on the forecast-table payload: `calibration: {method, realized_coverage, n_cycles}`.
- Analyst-facing copy in `ForecastTablePanel`: *"Bands calibrated on 6 closed cycles — 78% of actuals
  landed inside the 80% band."* An honest 78% builds more trust than a silent nominal 80%.

**Effort:** M. **Impact:** High — every number in the product gets a defensible error bar.

---

## Phase 2 — Add the one model class that is genuinely missing

The engine roster (`backend/app/domain/engines/`: ARIMA, ETS, Theta, Prophet, Croston, TSB, linear,
naive, seasonal-naive, mean, zero) is a complete *classical univariate* set. There is **no
cross-series model** — verified: the only sklearn import in the repo is `LinearRegression` at
`engines/linear.py:6`, and there is no LightGBM/XGBoost/torch anywhere.

That matters because an FP&A panel is hundreds of short, correlated series. Every line is forecast in
isolation today, and new/short lines fall back to `linear` or `average`.

**2.1 Global gradient-boosted panel model** (the M5-winning recipe, scoped to FP&A)
- Add `lightgbm` to `backend/requirements.txt`, then regenerate `requirements.lock` via
  `backend/scripts/lock-deps.sh` (CI installs with `--require-hashes`).
- New `backend/app/domain/engines/global_gbm.py` implementing `IForecastModel`
  (`engines/base_model.py:174`) — no new contract needed.
- New `backend/app/domain/engines/features.py`: lags (1,2,3,12), rolling mean/std, fiscal-period
  one-hots from `seasonal_period_length()` / `make_period_labels()` (so 4-4-5 works), category /
  business-unit target encoding, and driver columns reusing
  `backend/app/services/driver_exog.py:70 build_exog_for_line`.
- Fit **once across all line items**, predict per line. Three quantile objectives
  (`alpha=0.1/0.5/0.9`) give native, asymmetric P10/P50/P90 — the first non-symmetric intervals in
  the product.
- Declare `cost_class="expensive"`, a high `complexity_rank`, and `supports_exog=True` so it flows
  through the **existing** two-stage screen and 1-SE Occam tie-break. It wins only where it earns it.
  No selection-policy changes — that discipline is the app's best asset.
- Cache the fitted global model per version so per-line `predict` is cheap.

**2.2 Cheap wins in the existing grid**
- `backend/app/domain/engines/ets.py:51` `_candidate_specs` tries additive error and additive
  seasonality only. Add **multiplicative error/seasonality** and an optional **Box-Cox/log**
  transform. Revenue with proportional seasonality is the most common FP&A series shape and
  currently cannot be fit well.
- Optimize Croston/TSB smoothing constants — `α` and `β` are hardcoded at `0.1`
  (`engines/croston.py:114,192`); a small grid searched under the same CV costs almost nothing.

**2.3 Ensemble by default, not by request**
- `run_ensemble` is a manually-invoked skill; `generate_baseline` always picks a single winner.
- Promote combination into the pipeline: when the top-2 models are inside the 1-SE band, blend them
  (inverse-MASE weights). `_pick_best` (`model_registry.py:154`) already computes exactly that band.
- Fix two real bugs while in here: `backend/app/domain/skills/run_ensemble.py:163` hardcodes
  `pd.Timestamp(p + "-01")` (breaks under 4-4-5), and its `+10` confidence bonus diverges from
  `backend/app/services/confidence.py`, which is meant to be the single source of truth.
- Also fix `forecast_pipeline.py:137,262`, which hardcode `m = 12` for the MASE denominator and
  ignore the fiscal calendar the rest of the stack respects.

**Effort:** L. **Impact:** High — accuracy on short/new/correlated lines, plus real asymmetric bands.

---

## Phase 3 — Make the rolling forecast actually roll

`backend/app/workers/arq_worker.py:175` is `functions = [run_generate_baseline]` with **no
`cron_jobs`**. Every refresh, pull, and report in the product is a manual click. For a product whose
name is "Rolling Forecast", this is the biggest promise gap.

**3.1 Scheduled work**
- Add `WorkerSettings.cron_jobs` in `backend/app/workers/arq_worker.py`:
  - nightly scheduled pull via the existing `integrations/warehouse|erp` adapters,
  - auto-regenerate the draft when new actuals land (`AccuracySnapshotService.on_actuals_ingested`
    is already the trigger point — `backend/app/services/accuracy_snapshot.py`),
  - monthly accuracy report via the existing `auto_accuracy_report` skill,
  - driver freshness sweep (`GET /drivers/{id}/freshness` already exists).
- Raise `max_jobs=1` or split long forecasts onto their own queue — one slow forecast currently
  blocks everything.

**3.2 Move blocking work off the request thread**
Currently synchronous and shouldn't be: warehouse/ERP pull (`backend/app/api/integrations.py`),
document embedding (`POST /context/upload` — `Document.status` already has a `processing` state that
nothing ever processes), board-pack render + SMTP send (`backend/app/api/executive.py`),
`POST /panel/rescore-all`, `POST /heuristics/run`.

**3.3 Notifications**
- New `Notification` model + `GET/POST /api/notifications` + a bell in
  `frontend/src/components/common/Header.tsx`.
- Triggers: approval pending for you, new actuals ingested + what moved, driver input overdue
  (`is_late` already computed on `DriverInput`), anomaly detected, forecast job finished.
- Today the only outbound message in the entire backend is a manual board-pack email.

**Effort:** M. **Impact:** High — converts a manual tool into a system that works while you sleep.

---

## Phase 4 — Rebuild the analyst workspace

**4.1 More than one panel at a time**
- `frontend/src/store/panelStore.ts:9` holds a single `panelType`. Opening Explainability closes the
  Forecast Table — the two things an analyst most needs side by side.
- Change to a small panel **stack** (max 2 side-by-side + a maximize mode). `AppLayout.tsx:70` caps
  panels at `lg:max-w-[70%]`; add a full-width mode for grid work.
- Cache `panelData` by `(panelType, params)` so switching back doesn't refetch and lose scroll.

**4.2 Saved views and shareable state**
- Every filter in `ForecastTablePanel` / `AnomalyPanel` is local `useState`, gone on close.
- `frontend/src/components/common/PanelUrlSync.tsx` syncs only `?panel=<type>` — a link you send a
  colleague opens *their* version with *no* filters.
- Serialize panel params into the URL, and add named saved views (`rf_saved_views` in
  `localStorage` first; a `SavedView` table later if it sticks).
- Persist `AnomalyPanel` dismissals server-side — they're a client-side `Set` today and come back
  every time the panel reopens.

**4.3 Make `DataTable` an FP&A grid**
`frontend/src/components/ui/DataTable.tsx` has sorting, virtualization, CSV/XLSX, expandable rows.
It's missing what analysts reach for hourly:
- **Frozen first column / column pinning** — non-negotiable for a 12–18 month horizontal scroll.
- Per-column filters + a free-text search box.
- Built-in row selection + a bulk action bar (`ReviewDashboardPanel` hand-rolls its own checkboxes
  outside `DataTable`; the forecast table, anomalies, and overrides have no multi-select at all).
- A totals/subtotal footer row and conditional-format heatmap on variance columns.
- Keyboard cell navigation and **copy selection as TSV** so it pastes into Excel.
- **Fix the virtualization hole**: it is disabled exactly where it's needed —
  `DataTable.tsx:163` turns it off whenever `renderExpandedRow` is set, which is the forecast table
  and the review dashboard, the two biggest grids in the app.
- Export currently ships only the client-loaded page (`exportRows = rows`). Add a server-side
  "export full version" endpoint.

**4.4 Undo and safe destructive actions**
- Batch approve/reject and "Accept all AI recommendations" (`ReviewDashboardPanel`) fire immediately,
  with no preview and no confirm. One click can approve dozens of lines irreversibly.
- Add an undo toast (5–10s) backed by a real reversal endpoint, and a preview step on batch actions.
- Replace `window.confirm` (`Sidebar.tsx:322`) with a proper dialog primitive.

**4.5 Keyboard and performance**
- The only global key handler in the entire frontend is Esc/Tab in `SlidePanel.tsx:70`. Add a ⌘K
  command palette (open panel, switch version, jump to line item, focus composer) and row-level
  approve/flag shortcuts.
- Zero `React.lazy` / `React.memo` in `frontend/src` — all 17 panels (~11k lines) plus Recharts are
  statically imported in `PanelContainer.tsx`. Code-split per panel.
- Gate the 60s panel polls and the 2s `StatusCard` job poll on `document.hidden`.

**Effort:** L. **Impact:** High — this is where the analyst's day actually happens.

---

## Phase 5 — Surface the intelligence that already exists

The backend computes far more than the UI shows. This phase is mostly plumbing, and it is the
cheapest trust-per-hour in the plan.

**5.1 "Why this number?" drawer on every cell**
One drawer, fed by data that is already computed and serialized:
- Model chosen, the runner-up, and the margin — `ModelSelectionResult.to_dict()`
  (`model_registry.py:69`) already carries the full `comparisons` array with per-fold scores.
- Outliers winsorized (`ModelMetadata.cleaned_periods`), structural break detected and history
  truncated (`ModelMetadata.structural_break_detected`).
- Exog admitted or rejected **and why** — `_evaluate_plain_vs_exog` already returns
  `exog_rejected_reason` and the admission band (`forecast_pipeline.py:208-217`).
- Reconciliation delta — `pre_reconcile_p50` is stored on every row precisely for this.
- Override history and calibration status.

**5.2 Driver evidence card**
`DriverLink` carries `coefficient`, standard error, `t_stat`, `p_value`, `p_value_adj` (FDR), `r2`,
`n_obs`, `hac_lags`, `transform`, and a full `diagnostics` JSON. `DriverDiscoveryRun` stores the
ranked candidates **including the rejects**. Show the analyst the evidence, not just the link — this
is the difference between "the model says" and "here's why, and here's what didn't make the cut."

**5.3 Fix the agent's self-description**
`backend/app/orchestration/master_agent.py:36` still tells the model it has *"4 algorithms (ARIMA,
Prophet, ETS, Linear)"*. The registry has eleven. The agent is actively under-selling the product to
its own users. Generate the capability section from `registry.list_models()` instead of hardcoding it.

**Effort:** M. **Impact:** High — the rigor already exists; it just isn't reaching anyone.

---

## Phase 6 — Time-to-value and everyday CX

**6.1 Onboarding**
- Empty states are dead ends: `ForecastTablePanel` shows "No forecast data to display" with no CTA.
  Every empty state gets the next action ("Upload actuals", "Generate a baseline").
- A first-run guided path: upload → plan → generate → review, with progress.
- The Comparison nav item silently vanishes when fewer than two versions exist
  (`Sidebar.tsx:93`) — show it disabled with a reason instead.

**6.2 Chat discovery**
- `WelcomeScreen`'s 4 suggestions disappear after the first message and never return.
- `InputBar.tsx` has no slash commands, no autocomplete, no prompt history (no ↑ recall).
- Add `/` commands mapped to the 30 registered skills, `@` mentions for line items / drivers /
  versions, and follow-up suggestion chips after each response.
- Let panels push a prompt into the composer. Today `recommendedActions.ts:106` falls back to
  `navigator.clipboard.writeText` and expects the analyst to paste it themselves.

**6.3 Excel-shaped ergonomics**
- `DriverInputPanel` is 30 individual `<input>`s hard-capped at 30 rows. Make it a paste-able grid.
- Copy/paste TSV both directions (pairs with 4.3).

**6.4 Prove the value**
- FVA is already computed (`accuracy_snapshot.compute_fva`) and naive/seasonal-naive benchmarks are
  stored per vintage — the app can prove it beats a naive forecast and never says so.
- A value dashboard: cycle time trend, FVA vs naive, override rate falling, % of review time spent on
  flagged lines (goal G3 in the PRD, currently unmeasured in-product).

**Effort:** M. **Impact:** Medium-High — drives adoption and renewal conversations.

---

## Explicitly out of scope

Per your direction, and noted so they aren't silently dropped:
- **Enterprise reach** — vendor connectors (NetSuite/SAP/Snowflake/BigQuery), multi-tenancy
  (blocked at the schema level anyway: `LineItem.account_code` is globally unique), Excel add-in.
- **i18n completion** — only 5 of 17 panels call `useI18n`; switching to Spanish yields a bilingual
  UI. Worth fixing, not now.
- **Dark mode** — `index.html:13` reads `rf_theme`, nothing ever writes it, there is no
  `[data-theme="dark"]` block and zero `dark:` classes. It is dead code; delete or finish it.
- Repo hygiene: committed `rolling_forecast.db` (10MB), four test DBs, `coverage.json`, 60+ files in
  `backend/uploads/`.

---

## Sequencing

| Phase | Effort | Impact | Depends on |
|---|---|---|---|
| 1 — Calibration loop | M | High | — |
| 2 — Global GBM + ETS/ensemble | L | High | 1 (shares CV residual plumbing) |
| 3 — Scheduling + notifications | M | High | — (parallel with 1) |
| 4 — Analyst workspace | L | High | — (parallel; frontend) |
| 5 — Surface the intelligence | M | High | 1, 4.1 |
| 6 — Onboarding + CX | M | Med-High | 4 |

Recommended start: **Phase 1 and Phase 4.1–4.3 in parallel** — one backend track, one frontend track,
no shared files, and together they deliver a visibly better product in one cycle.

---

## Verification

**Phase 1 (calibration)**
- New `backend/tests/test_interval_calibration.py`: on synthetic series with known noise, assert
  empirical coverage of calibrated bands lands within ±5pp of 80%, and that it beats the analytic
  bands it replaces.
- Assert `bounds_method` round-trips and that the analytic fallback fires on short history.
- Regression: `backend/tests/test_mint_coherence.py` must still pass — MinT rewrites parent intervals
  from `s'Ws` and must compose with conformal leaves.

**Phase 2 (models)**
- Extend `backend/tests/test_model_registry.py`: GBM registers, is gated by the two-stage screen,
  and cannot win inside the 1-SE band against a simpler model (the Occam guarantee must hold).
- Backtest harness over `test_actuals_data.csv`: report MASE and pinball per model, old vs new
  roster. Ship only if the new roster wins on MASE **and** does not regress pinball.
- `pytest backend/tests/test_phase1_selection.py` unchanged — selection policy must not drift.

**Phase 3 (scheduling)**
- `docker compose up` with `REDIS_URL` set; assert cron jobs register and a forced run produces a new
  draft version + notification rows.
- Assert `/integrations/warehouse/pull` returns a job id rather than blocking.

**Phases 4–6 (frontend)**
- `cd frontend && npm run typecheck && npm run lint && npm test`.
- Extend `frontend/e2e/smoke.spec.ts` — currently only 2 tests, both `test.skip`'d unless
  `E2E_BASE_URL` is set. Add: open two panels side by side, apply and reload a saved view, bulk
  approve then undo, ⌘K palette, export full version. Wire a `webServer` block so they run in CI.
- Add the first component tests (no panel or chat component has one today), starting with
  `DataTable` pinning/selection/copy.
- Run the app and verify visually via the browser preview: forecast table with frozen column +
  explainability open beside it; calibration badge; "Why this number?" drawer.
- `frontend/src/theme/contrast.test.ts` must still pass for any new tokens.

**Whole-app**
- `cd backend && pytest -q --cov=app --cov-fail-under=60` (the CI gate).
- `ruff check app tests alembic && mypy app alembic --config-file pyproject.toml`.