---
name: get_forecast_settings
description: >
  Show a company's forecast settings (selection metric, default horizon,
  materiality share, outlier cleaning, conformal calibration, driver
  forecasting, benchmark and global cross-series models, ...) and whether
  each is a company override or the global default.
required_role: generate
version: "1.0"
tags: [forecasting, settings, configuration]
parameters:
  - name: business_unit
    type: string
    description: >
      Company / business unit name or id. Required for a user who works across
      companies; otherwise defaults to the user's own company.
---

# Get Forecast Settings

## When to Use
- User asks how forecasting is configured: "what selection metric do we use?"
- User asks why a model was or wasn't considered (e.g. benchmark or global models off)
- Before proposing a settings change with update_forecast_settings

## Behavior
Read-only. Returns every setting with its effective value, type, and whether
it comes from a company override or the global default.

## Examples
- "What are our forecast settings?"
- "Is outlier cleaning on for us?"
- "Show the forecast configuration for North America"
