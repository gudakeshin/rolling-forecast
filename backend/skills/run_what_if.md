---
name: run_what_if
description: >
  Create a what-if forecast scenario by shocking causal drivers
  (e.g. headcount -10%). Clones the base version, writes scenario driver
  values, and perturbs linked line items via coefficients.
required_role: generate
version: "1.0"
tags: [scenario, what-if, drivers, sensitivity]
parameters:
  - name: base_version_id
    type: string
    description: Base forecast version (uses active if omitted)
  - name: scenario_label
    type: string
    description: Label for the new scenario
    required: true
  - name: shocks
    type: array
    description: "List of {driver_id or driver_key, mode, value}"
    required: true
    items:
      type: object
  - name: method
    type: string
    description: "links (coefficients, instant) or foundation (re-forecast linked lines with Chronos-2)"
    enum: [links, foundation]
    default: links
  - name: open_panel
    type: boolean
    description: Open the what-if panel after creation
---

# Run What-If

## When to Use
- "What if headcount grows 10% slower?"
- "Shock volume down 5% and show the scenario"
- Sensitivity / scenario planning off causal drivers

## Behavior
1. Resolve drivers by id or key
2. Clone base forecast version immutably
3. Persist shocked driver series as value_type=scenario
4. Perturb linked line p50 via coefficient/elasticity (method=links), or by
   the change between two Chronos-2 forecasts of the line, one under the base
   driver path and one under the shocked path (method=foundation). The band
   keeps its own half-widths around the moved point. Lines Chronos-2 cannot
   serve (under 12 months of history, no usable driver column, service down)
   fall back to links, and the result says so.
5. Open what-if / forecast panel

## Examples
- "What if headcount is 10% lower?" →
  scenario_label=hc_down_10, shocks=[{driver_key: headcount_na, mode: pct, value: -10}]
- "Replace oil price with 80 for the scenario" → mode=replace
- "Re-forecast with headcount down 10%, not just the coefficient" →
  method=foundation, shocks=[{driver_key: headcount_na, mode: pct, value: -10}]
