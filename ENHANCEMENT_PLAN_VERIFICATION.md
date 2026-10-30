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
| 2 | Ship criterion: new roster wins on MASE and does not regress pinball | `backend/scripts/backtest_roster.py` (6-month holdout), re-run after the GBM moved to origin-relative targets: blend+GBM on `test_actuals_data.csv` (33 series) MASE −10.8%, pinball −13.3%; on the seed files (77 series) MASE −19.8%, pinball −20.4% (passes both); blending alone regresses pinball on the seed files, and GBM alone regresses pinball on test_actuals (each fails alone). **Shipped:** both on by default, together (`config.py`); per-company switch in the AnalysisProfile |
| 2 | Stress test beyond the seed data | `backend/scripts/synthetic_panel.py` (80 series, 8 shapes: trend, additive/multiplicative seasonal, level shift, intermittent, random walk, short, heavy-tailed) through `backtest_roster.py --group-part 1`: blend+GBM MASE −24.3%, pinball −59.7%, 80% coverage 0.54 → 0.62 (passes). Per shape, MASE improves or holds on 6 of 8; heavy-tailed series are flat within noise (0.457 → 0.464). **Remaining weak spot:** clean linear trends, scaled MASE 0.37 → 0.43 (was 0.62 before the GBM learned origin-relative targets, which let the trees extrapolate a trend; `test_steady_trends_are_extrapolated_not_flattened`). Most of the residual comes from blending (0.40 with blending alone). Level shifts see pinball +6% while MASE improves. Companies dominated by steady trends can switch both off in their AnalysisProfile |

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

## Appendix — line-by-line audit of `ENHANCEMENT_PLAN.md`

Every requirement bullet of the plan, by ID (item number + bullet letter, in
plan order), with its evidence. `backend/tests/test_plan_audit.py` re-extracts
the bullets from `ENHANCEMENT_PLAN.md` and fails if any ID is missing here or
has no evidence, so the plan and this audit cannot drift apart.

<!-- plan-audit:start -->
| ID | Evidence |
|---|---|
| 1.1.a | `test_evaluate_cv_returns_residuals_keyed_by_horizon` |
| 1.1.b | `test_registry_exposes_residuals_for_the_model_actually_used` |
| 1.2.a | `interval_calibration.conformal_bands` / `apply_bands`: `test_covers_target_on_held_out_errors`, `test_thin_horizon_borrows_pooled_residuals_and_says_so`, `test_refuses_to_calibrate_on_thin_evidence` |
| 1.2.b | Wired in `forecast_pipeline` (calibration first, exog inflation compounds on top — see deviation above): `test_pipeline_tags_bounds_method_and_keeps_the_ordering_invariant` |
| 1.2.c | `test_pipeline_tags_bounds_method_and_keeps_the_ordering_invariant` |
| 1.2.d | `test_short_history_falls_back_to_the_analytic_band` |
| 1.3.a | `realized_coverage_by_line` reads `within_p10_p90`: `test_why_this_number::test_realized_coverage_aggregated_across_vintages` |
| 1.3.b | `test_realized_coverage_corrects_the_cv_band`, `test_rescale_is_clamped_against_noisy_coverage_estimates` |
| 1.4.a | `test_what_if_band_widens_by_elasticity_estimation_error` |
| 1.5.a | `calibration_summary` on the table payload (calibrated lines, realized coverage, lines with closed cycles, target): `test_forecast_table_reports_calibration_provenance` |
| 1.5.b | `CalibrationBanner.test.tsx` |
| 2.1.a | Deviation: scikit-learn `HistGradientBoostingRegressor` (already pinned) instead of adding `lightgbm` |
| 2.1.b | `test_global_gbm::test_fit_predict_round_trip`, `test_fit_raises_without_panel` |
| 2.1.c | Features live in `global_gbm.py` / `panel_forecast.py`, expressed relative to the forecast origin (level and 12-month mean absolute scale): `test_lag_rolling_features_shape_and_values`, `test_fiscal_features_445_period`, `test_builds_context_for_a_simple_panel` (category one-hots), `test_held_out_fold_driver_columns_use_origin_restricted_overlay`, `test_steady_trends_are_extrapolated_not_flattened` |
| 2.1.d | `test_builds_context_for_a_simple_panel` (one fit, three quantile models), `test_predict_enforces_monotonicity_on_crossed_quantiles`, `test_steady_trends_are_extrapolated_not_flattened` (predictions are mapped back from origin-relative to level, within 2% of the true trend) |
| 2.1.e | `test_two_stage_screen_gates_expensive_admission`, `test_exact_tie_prefers_lower_complexity_rank`; drivers enter as panel feature columns rather than through the per-line exog interface |
| 2.1.f | Panel built once per generation run and reused per line: `test_generation_builds_the_panel_once_and_reuses_it_per_line` (one build and no extra model fits across a multi-line generation), `test_evaluate_one_looks_up_precomputed_panel_cv` |
| 2.2.a | `test_ets_grid_offers_multiplicative_and_log_specs_for_positive_data`, `test_ets_picks_a_proportional_spec_on_proportional_seasonality` |
| 2.2.b | `test_croston_alpha_is_searched_not_fixed`, `test_tsb_alpha_beta_are_searched` |
| 2.3.a | Blending in `generate_baseline`'s pipeline, on by default: `test_blends_top2_when_enabled`, `test_registered_by_default` |
| 2.3.b | `test_blends_top2_when_enabled`, `test_no_blend_when_disabled`; ship criterion: `scripts/backtest_roster.py` |
| 2.3.c | `test_run_ensemble_handles_fiscal_periods_and_uses_shared_confidence` |
| 2.3.d | `test_exog_mase_scale_follows_the_fiscal_calendar` |
| 3.1.a | `test_scheduled_jobs_are_registered_with_the_worker`; live Redis check (forced nightly sync) |
| 3.1.b | `test_worker_queues_are_split` |
| 3.2.a | `test_background_tasks.py` (202 + job id, inline fallback, document indexing, distribute access check); live Redis check |
| 3.3.a | `test_notifications.py`, `NotificationBell.test.tsx` |
| 3.3.b | `test_submit_for_approval_notifies_first_level_reviewers`, `test_actuals_ingest_notifies_can_generate_users_with_top_mover`, `test_driver_overdue_notifies_only_the_forms_own_company`, `test_anomaly_sweep_notifies_reviewers_and_survives_unreadable_versions`, `test_worker_runs_task_and_notifies` |
| 3.3.c | Notifications now go out for the triggers in 3.3.b, not only the board-pack email |
| 4.1.a | `driverPaste.test.ts` (two panel stores open independently) |
| 4.1.b | `PanelUrlSync.test.tsx` (maximize toggling, wide mode, pin beside), e2e "two panels side by side", e2e "maximize shows one panel full width, restore brings both back" |
| 4.1.c | `panelDataCache.test.ts` |
| 4.2.a | `PanelUrlSync.test.tsx` (filters survive in the URL), `test_saved_views.py` |
| 4.2.b | `PanelUrlSync.test.tsx`, e2e "a shared link reopens the panel with its filters, across reloads" |
| 4.2.c | `PanelUrlSync.test.tsx`, `test_saved_views.py`, `savedViewsStore.test.ts`, e2e saved view across reload, e2e "a shared link reopens the panel with its filters, across reloads" |
| 4.2.d | `test_anomaly_dismissal_persists_across_reloads` |
| 4.3.a | `DataTable.test.tsx` |
| 4.3.b | `DataTable.test.tsx` (pinned columns) |
| 4.3.c | `DataTable.test.tsx` (per-column filters, free-text search) |
| 4.3.d | `DataTable.test.tsx` (row selection + bulk action bar) |
| 4.3.e | `DataTable.test.tsx` (totals footer, `heatmapBackground`) |
| 4.3.f | `DataTable.test.tsx` (arrow-key cell navigation, copy as TSV) |
| 4.3.g | `DataTable.test.tsx` (still windows when expandable rows exist) |
| 4.3.h | `test_forecast_export.py`, e2e full export |
| 4.4.a | `reviewShortcuts.test.ts` (`previewLines`), e2e bulk approve shows a preview |
| 4.4.b | `test_review_undo.py`, e2e preview then undo |
| 4.4.c | `confirmStore.test.ts`; `Sidebar.tsx` uses `confirmDialog` |
| 4.5.a | e2e command palette; palette sections for panels, versions, line items, composer focus; `reviewShortcuts.test.ts` |
| 4.5.b | Live: production build emits one chunk per lazy panel |
| 4.5.c | `StatusCard.test.tsx` |
| 5.1.a | `test_why_this_number.py` |
| 5.1.b | `test_auto_selection_persists_comparisons_into_model_metadata`, `test_model_selection_and_exog_surfaced_from_metadata_parameters` |
| 5.1.c | `test_basic_fields_and_no_extras_when_nothing_special_happened`, `test_model_selection_and_exog_surfaced_from_metadata_parameters` |
| 5.1.d | `test_model_selection_and_exog_surfaced_from_metadata_parameters` |
| 5.1.e | `test_reconciliation_delta_only_shown_when_mint_actually_moved_it` |
| 5.1.f | `test_override_history_spans_versions_for_the_same_cell`, `test_realized_coverage_aggregated_across_vintages` |
| 5.2.a | `test_phase9_discovery.py`; `DriversPanel.tsx` shows t, adjusted p and rejected candidates |
| 5.3.a | `test_agent_model_list_is_generated_from_the_registry`, `test_agent_prompt_lists_the_companys_models` |
| 6.1.a | `GettingStarted.test.tsx` (`EmptyForecastState` actions by permission) |
| 6.1.b | `GettingStarted.test.tsx` (`firstRunSteps`), e2e empty workspace |
| 6.1.c | `useNavItems.test.tsx` |
| 6.2.a | `InputBar.test.tsx` (follow-up chips after each reply) |
| 6.2.b | `InputBar.test.tsx` (slash menu, mentions, ↑ history) |
| 6.2.c | `InputBar.test.tsx` (a `/` command per permitted registered skill; `@` line items, drivers and versions; chips) |
| 6.2.d | `composerStore.test.tsx` |
| 6.3.a | `driverPaste.test.ts` (no 30-row cap) |
| 6.3.b | `driverPaste.test.ts` (paste in), `DataTable.test.tsx` (copy out as TSV) |
| 6.4.a | `test_value_metrics.py` (FVA vs naive), Accuracy Tracking FVA tiles |
| 6.4.b | `test_value_metrics.py`, `ValueMetrics.test.tsx` |
<!-- plan-audit:end -->
