---
name: preview_forecast
description: >
  Instant forecast preview of an uploaded actuals dataset from the Chronos-2
  foundation model: one call over every line, in seconds, nothing saved. A
  first look while the full baseline is generated as usual.
required_role: null
version: "1.0"
tags: [forecast, preview, foundation-model, chronos]
parameters:
  - name: dataset_id
    type: string
    description: Actuals dataset to preview (defaults to the most recent one)
  - name: horizon_months
    type: integer
    description: Periods ahead to preview (1-24)
    default: 12
---

# Preview Forecast

## When to Use
- "Give me a quick look at what these actuals imply"
- Right after the user uploads actuals, before the baseline has run
- "What does next year look like, roughly?" when no version exists yet

## Behavior
1. Resolve the dataset (given, the session's last upload, or the newest the user can see)
2. One Chronos-2 call over every line with at least 3 periods; no CV, no drivers
3. Summarise the largest lines and open the instant-preview panel
4. Say plainly it is a preview: the forecast to review and publish comes from generate_baseline

If the foundation service is down, say so and suggest generating the baseline.

## Examples
- "Preview the upload" → preview_forecast()
- "Quick 6-month look at dataset X" → preview_forecast(dataset_id=X, horizon_months=6)
