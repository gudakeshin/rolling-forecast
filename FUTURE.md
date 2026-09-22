# Deferred / future work

Items from the enterprise production-readiness plan that are intentionally
not blocking a desktop enterprise rollout, or that shipped beyond the original
deferral list:

| Item | Status | Notes |
|---|---|---|
| Responsive / mobile layouts | Deferred | Desktop FP&A tool; some responsive tweaks exist but no mobile-first redesign |
| Light theme | Shipped | `themeStore` + CSS tokens; originally deferred |
| i18n (en/es) | Shipped | `frontend/src/i18n`; originally deferred |
| Full MinT hierarchical reconciliation | Shipped | `mint_full` default — summing matrix S, shrinkage residual corr, MinT projection, s′Ws intervals; diagonal kept as `mint_diagonal` |
| Raise CI coverage gate above 25% | Shipped | `--cov-fail-under=40` (measured suite ~47%) |
| WCAG AA automated contrast audit | Shipped | `frontend/src/theme/contrast.test.ts` — dark + light token pairs |
| Remaining clickable-div → button sweep | Shipped | No `role="button"` left in `frontend/src`; panels use real `<button>` |
