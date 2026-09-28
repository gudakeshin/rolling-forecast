---
name: discover_drivers
description: >
  Statistically discover which causal drivers explain a P&L line item. Scans existing
  driver series across a lag grid, applies FDR (Benjamini-Hochberg) correction plus
  elasticity, sign-prior and placebo gates, and records candidate links for human
  review. Never activates a link — promotion stays a human decision.
required_role: manage_drivers
version: "1.0"
tags: [drivers, causal, discovery, statistics, candidates]
parameters:
  - name: action
    type: string
    description: "Action: run a new scan, or get a prior run"
    required: true
    enum: [run, get]
  - name: line_item_id
    type: integer
    description: Line item to explain (required for action=run)
  - name: run_id
    type: string
    description: Discovery run id (required for action=get)
  - name: max_lag
    type: integer
    description: Highest driver lag to test (default min(6, n/6))
  - name: alpha
    type: number
    description: FDR significance threshold (default 0.05)
  - name: max_survivors
    type: integer
    description: Cap on candidate links written (default 3)
  - name: enable_placebo
    type: boolean
    description: Run the block-bootstrap placebo gate (default true)
---

# Discover Drivers

## When to Use
- User asks what drives a line item, or which drivers correlate with it
- User asks to find / discover / search for drivers statistically
- User wants candidate driver links proposed rather than asserted by hand

Do **not** use this to assert a link the user already knows about — that is
`manage_drivers` with `action=assert_link`.

## Behavior
1. Aligns the line item's actuals with each candidate driver's actual series,
   testing lags 0..min(6, n/6) with `driver(t-lag) → line(t)`.
2. Requires at least 18 overlapping periods; both sides are winsorized at the
   MAD fence first (spikes are damped, never deleted).
3. Fits OLS with Newey–West (HAC) standard errors when available.
4. Gates each test: BH-adjusted p < alpha, |elasticity| ≥ 0.05 when computable,
   coefficient sign must match the driver-type prior, the relation must also hold
   on first differences with the same sign, and a bootstrap placebo must still
   reject. Keeps the best lag per driver, capped at `max_survivors`.
5. Writes a `DriverDiscoveryRun` (full ranked list including rejects and their
   reasons) plus **candidate** `DriverLink` rows. Candidates from earlier
   discovery runs on the same line are marked `superseded`.

## Guardrails
- Discovery only ever produces `candidate` links. A human with override
  permission must promote a link before it influences any forecast.
- Statistical association is not causation — always surface the adjusted p-value,
  the lag, and the number of observations when reporting results.
- Two unrelated trending series correlate in levels. The difference gate exists to
  catch exactly that, so do not suggest disabling it to "find more drivers".
- Sign priors are a small hardcoded default (headcount→expense positive,
  volume→revenue positive, etc.). A configurable `sign_priors` table is follow-up.

## Examples
- "What drives Salaries & Benefits?" → action=run, line_item_id=4
- "Find drivers for line 12, test up to 3 lags" → action=run, line_item_id=12, max_lag=3
- "Show me discovery run abc-123" → action=get, run_id=abc-123
