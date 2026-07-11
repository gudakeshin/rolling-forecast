# Deferred / future work

Items from the enterprise production-readiness plan that are intentionally
not blocking a desktop enterprise rollout, or that shipped beyond the original
deferral list:

| Item | Status | Notes |
|---|---|---|
| Responsive / mobile layouts | Deferred | Desktop FP&A tool; some responsive tweaks exist but no mobile-first redesign |
| Light theme | Shipped | `themeStore` + CSS tokens; originally deferred |
| i18n (en/es) | Shipped | `frontend/src/i18n`; originally deferred |
| Full MinT hierarchical reconciliation | Deferred | Leaf-scale + topo recompute + `linear_aggregation` bounds ship today |
| Raise CI coverage gate above 25% | Open | Incrementally raise `--cov-fail-under` as suites grow |
| WCAG AA automated contrast audit | Open | Manual token review done; add contrast CI later |
| Remaining clickable-div → button sweep | Open | Core primitives use `Button`/`Pressable`; some panel rows still use `role="button"` |
