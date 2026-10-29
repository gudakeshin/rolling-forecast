# Enhancement plan — verification

Each item of the enhancement plan (Phases 1–6), its acceptance criteria as the
plan states them, and the evidence that they hold. "Live" means checked against
a running stack, not only unit tests. Backend tests are under `backend/tests/`,
frontend tests next to their source (`*.test.ts[x]`), end-to-end tests in
`frontend/e2e/`. Every listed test passes in CI (`.github/workflows/ci.yml`).

Deviations from the plan text are called out in **Deviation** rows.

## Phase 1 — Calibration loop

| Item | Criterion (plan) | Evidence |
|---|---|---|
| 1.1 | CV returns per-horizon fold residuals | `test_interval_calibration::test_evaluate_cv_returns_residuals_keyed_by_horizon` |
| 1.1 | Residuals carried through comparison/selection results | `test_interval_calibration::test_registry_exposes_residuals_for_the_model_actually_used` |
| 1.2 | Split-conformal bands per horizon; calibrated coverage within ±5pp of 80% and better than the analytic band | `test_covers_target_on_held_out_errors`, `test_beats_the_parametric_band_under_heavy_tails`, `test_bands_widen_with_horizon` |
| 1.2 | Pooled residuals when a horizon is thin; refuses on thin evidence | `test_thin_horizon_borrows_pooled_residuals_and_says_so`, `test_refuses_to_calibrate_on_thin_evidence` |
| 1.2 | `bounds_method` tagged `conformal` / `conformal+exog_variance_inflated` | `test_pipeline_tags_bounds_method_and_keeps_the_ordering_invariant` |
| 1.2 | Analytic fallback on short history, said in metadata | `test_short_history_falls_back_to_the_analytic_band` |
| 1.2 | Composes with MinT reconciliation | `test_reconciliation_restores_the_non_negative_p10_floor`, `test_mint_coherence.py` |
| 1.2 | **Deviation:** calibration runs *before* the exog delta-method inflation, so driver uncertainty compounds on the calibrated band (the plan's stated goal) rather than being overwritten by it | `forecast_pipeline.py` comment at the calibration block |
| 1.3 | Realized vintage coverage blended in after ≥3 closed cycles | `test_realized_coverage_corrects_the_cv_band`, `test_rescale_is_clamped_against_noisy_coverage_estimates`; `conformal_realized_min_cycles = 3` |
| 1.4 | What-if shocks propagate uncertainty instead of restoring base bands | `test_phase6b_7::test_what_if_band_widens_by_elasticity_estimation_error`, `test_what_if_shock_moves_forecast_periods_by_fitted_beta_at_lag` |
| 1.5 | Table payload carries a calibration summary; honest analyst copy | `test_forecast_export::test_forecast_table_reports_calibration_provenance`, `CalibrationBanner.test.tsx` |

## Phase 2 — Models

| Item | Criterion (plan) | Evidence |
|---|---|---|
| 2.1 | Global cross-series model, fit once, native asymmetric P10/P50/P90 | `test_global_gbm.py` (round trip, monotone quantiles, 4-4-5 features), `test_panel_forecast.py` (incl. leakage guard on held-out driver columns) |
| 2.1 | Flows through the two-stage screen and 1-SE Occam tie-break; cannot beat a simpler model inside the band on a tie | `test_model_registry.py::TestGlobalGBMRegistration`, `test_exact_tie_prefers_lower_complexity_rank`, `test_two_stage_screen_gates_expensive_admission` |
| 2.1 | Features: lags, rolling stats, fiscal position (4-4-5 aware), category / business unit, driver columns | `test_global_gbm.py` (lag/rolling, 4-4-5 fiscal features), `test_panel_forecast::test_builds_context_for_a_simple_panel` (category columns), `test_held_out_fold_driver_columns_use_origin_restricted_overlay` |
| 2.1 | **Deviation:** `sklearn.HistGradientBoostingRegressor` (quantile loss) instead of `lightgbm`, to avoid a new pinned dependency; category / business unit / product line are one-hot encoded from the panel's own vocabulary rather than target-encoded, which avoids target leakage across CV folds | `global_gbm.py`, `panel_forecast._category_onehot` |
| 2.1 | Short/new lines can use it (plan: "new/short lines fall back to linear") | `test_sparse_line_selection.py` |
| 2.2 | ETS multiplicative error/seasonality and log (Box-Cox λ=0) transform | `test_plan_engine_behaviour::test_ets_grid_offers_multiplicative_and_log_specs_for_positive_data`, `test_ets_picks_a_proportional_spec_on_proportional_seasonality` |
| 2.2 | Croston/TSB smoothing constants searched, not fixed at 0.1 | `test_croston_alpha_is_searched_not_fixed`, `test_tsb_alpha_beta_are_searched` |
| 2.3 | Blend top-2 inside the 1-SE band, inverse-MASE weights | `test_ensemble_blending.py` |
| 2.3 | `run_ensemble` handles 4-4-5 periods; no ad-hoc +10 confidence bonus | `test_plan_engine_behaviour::test_run_ensemble_handles_fiscal_periods_and_uses_shared_confidence` |
| 2.3 | MASE denominator follows the fiscal calendar, not `m = 12` | `test_exog_mase_scale_follows_the_fiscal_calendar` |
| 2 | Ship criterion: new roster wins on MASE and does not regress pinball | `backend/scripts/backtest_roster.py` on the three seed datasets: blend+GBM MASE −11.8% / −8.8%, pinball −8.8% / −3.4% (passes); blending alone regresses pinball on two of three (fails). **Shipped:** both on by default, together (`config.py`); per-company switch in the AnalysisProfile |

## Phase 3 — Scheduling and notifications

| Item | Criterion (plan) | Evidence |
|---|---|---|
| 3.1 | Cron jobs: nightly pull, regenerate on new actuals, monthly accuracy report, driver freshness | `test_plan_engine_behaviour::test_scheduled_jobs_are_registered_with_the_worker`; **live** (local Redis + worker): all five registered, a forced nightly sync regenerated a draft (and only notified when a manual upload was pinned) |
| 3.1 | One slow forecast must not block everything | Separate task queue: `test_background_tasks::test_worker_queues_are_split` (forecasts 1 slot, tasks 4) |
| 3.1 | Worker actually starts | `test_worker_startup_hooks_are_awaitable` (the worker crashed at boot from 2026-07-11 until this fix); **live** start |
| 3.2 | Pulls, document embedding, board-pack email, rescore-all, reflection off the request thread; pull returns a job id | `test_background_tasks.py` (202 + job, inline without a queue, 503 when required but absent, worker success/failure/notification, fail-closed scope); **live:** pull, rescore-all and document indexing returned 202 and completed on the task worker |
| 3.3 | Notification model + API + header bell | `test_notifications.py`, `NotificationBell.test.tsx` (unread count, choosing a notification marks it read and opens its panel) |
| 3.3 | Trigger: approval pending for you | `test_notifications::test_submit_for_approval_notifies_first_level_reviewers`, `test_approving_level_one_notifies_level_two` |
| 3.3 | Trigger: new actuals ingested + what moved | `test_notifications::test_actuals_ingest_notifies_can_generate_users_with_top_mover` |
| 3.3 | Trigger: driver input overdue (own company only) | `test_scheduled_sweeps::test_driver_overdue_notifies_only_the_forms_own_company` — also fixed: forms were paired with every company's versions, and recipients were matched by legacy name only |
| 3.3 | Trigger: anomaly detected | `test_scheduled_sweeps::test_anomaly_sweep_notifies_reviewers_and_survives_unreadable_versions` — also fixed: the sweep now runs as a platform admin and one unreadable version no longer aborts it |
| 3.3 | Trigger: forecast / background job finished or failed | `test_background_tasks::test_worker_runs_task_and_notifies`, `test_worker_failure_is_recorded_and_notified`; **live:** forecast job notification linked the new version |

## Phase 4 — Analyst workspace

| Item | Criterion (plan) | Evidence |
|---|---|---|
| 4.1 | Two panels side by side | `driverPaste.test.ts` (panel stack), `PanelUrlSync.test.tsx` (pin current panel beside), e2e "two panels side by side" |
| 4.1 | Maximize mode / full width for grid work | `PanelUrlSync.test.tsx` (maximize a slot and toggle back; wide mode remembered per slot) |
| 4.1 | Panel data cached by (type, params): switching back doesn't refetch | `panelDataCache.test.ts`; e2e suite passes with the cache in place |
| 4.2 | Panel params serialized into the URL; a shared link opens the same panels with the same filters | `PanelUrlSync.test.tsx` (deep link restores both panels + filters, store changes written back, closed panel removed, bad params ignored) |
| 4.2 | Named saved views | `test_saved_views.py`, `savedViewsStore.test.ts`, e2e "saved view survives reload" |
| 4.2 | Anomaly dismissals persisted server-side | `test_dashboard_batching::test_anomaly_dismissal_persists_across_reloads` |
| 4.2 | **Deviation (beyond plan):** saved views are server-side and shareable within a company, not localStorage | migration 031 |
| 4.3 | Frozen column, per-column filters + search, row selection + bulk bar, totals footer, heatmap, keyboard cell navigation, TSV copy, virtualization with expanded rows | `DataTable.test.tsx` (pinning, selection, totals, TSV copy, virtualization, heatmap, per-column filters, arrow-key navigation) |
| 4.3 | Server-side "export full version" | `test_forecast_export.py` (all rows, filters, formula-injection guard, XLSX, 404 cross-company), e2e export |
| 4.4 | Undo backed by a real reversal endpoint; preview before batch actions; dialog instead of `window.confirm` | `test_review_undo.py`, e2e "bulk approve shows a preview and can be undone", `confirmStore.test.ts` |
| 4.5 | ⌘K palette | e2e "command palette opens a panel" |
| 4.5 | Row-level approve/flag shortcuts | `reviewShortcuts.test.ts` (key mapping, ignored while typing or with modifiers) |
| 4.5 | Per-panel code splitting | **Live:** `npm run build` emits a separate chunk for every `React.lazy` panel in `PanelContainer.tsx` |
| 4.5 | Polls paused while the tab is hidden | `StatusCard.test.tsx` (2s job poll stops when `document.hidden`, resumes when visible); same guard in ForecastTable / Review / Approvals |

## Phase 5 — Surface the intelligence

| Item | Criterion (plan) | Evidence |
|---|---|---|
| 5.1 | "Why this number?": model + runner-up, cleaning/breaks, exog admit/reject reason, reconciliation delta, override history, calibration | `test_why_this_number.py` (all seven cases) |
| 5.2 | Driver evidence incl. rejected candidates | `test_phase9_discovery.py` (FDR, placebo, sign priors, rejects kept on the run) |
| 5.3 | Agent's capability text generated from the registry, not hardcoded | `test_per_company_model_gates::test_agent_model_list_is_generated_from_the_registry` (equals the registry's selectable models, excludes benchmark/edge-case models, no "4 algorithms"), `test_agent_prompt_lists_the_companys_models` (follows the company's model switches) |

## Phase 6 — Time-to-value

| Item | Criterion (plan) | Evidence |
|---|---|---|
| 6.1 | Empty states give the next action; first-run guided path with progress; Comparison disabled with a reason | `GettingStarted.test.tsx`, `useNavItems.test.tsx`, e2e "empty workspace guides the first run" |
| 6.2 | `/` commands | `InputBar.test.tsx` (menu opens on `/`, Enter runs the highlighted command) |
| 6.2 | `@` mentions for line items | `InputBar.test.tsx` (debounced search, arrow + Enter inserts the name) |
| 6.2 | Prompt history (↑ recall) | `InputBar.test.tsx` (↑/↓ through sent prompts, drafts untouched, survives remount); added in this pass, it was missing |
| 6.2 | Follow-up suggestion chips | `InputBar.test.tsx` (context-aware chips, click sends, hidden when the reply has actions) |
| 6.2 | Panels push a prompt into the composer instead of the clipboard | `composerStore.test.tsx` |
| 6.3 | Paste-able driver grid, no 30-row cap; TSV both directions | `driverPaste.test.ts`, `DataTable.test.tsx` (TSV copy) |
| 6.4 | Value dashboard: cycle time trend, FVA vs naive, override rate | `test_value_metrics.py`, `ValueMetrics.test.tsx` |
| 6.4 | **Deviation:** "% of review time on flagged lines" is measured as the share of review *actions* on lines the AI flagged; the app records actions, not time spent | `value_metrics.py` (`flagged_review_share`) |

## Whole-app gates (plan "Verification")

- `pytest -q --cov=app --cov-fail-under=60`: 705 passed, coverage 75%.
- `ruff check app tests alembic` and `mypy app alembic`: clean.
- `npm run typecheck && npm run lint && npm test`: clean, **0 lint warnings** (the 18 earlier warnings, all from commits dated 2026-02-22 to 2026-09-20, were fixed).
- `npx playwright test` against a live stack: all e2e pass, in CI and locally on a fresh database.
- Migrations up/down/up on SQLite and Postgres.
