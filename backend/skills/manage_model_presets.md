---
name: manage_model_presets
description: >
  Create, update, or deactivate a saved model preset — a named shortcut that
  pins a specific forecasting algorithm (or restricts the auto-selection
  candidate pool) for reuse with generate_baseline's model_preset parameter.
  Admin only. Does not change the forecasting engine itself, only which
  algorithm(s) a future generate_baseline run considers.
required_role: admin
version: "1.0"
tags: [forecasting, models, presets, admin]
parameters:
  - name: action
    type: string
    description: "Action: create, update, deactivate"
    required: true
    enum: [create, update, deactivate]
  - name: preset_id
    type: string
    description: Preset id (required for update/deactivate)
  - name: name
    type: string
    description: Preset name (create, or rename on update)
  - name: description
    type: string
    description: Optional human-readable description
  - name: model_type
    type: string
    description: >
      'auto' (default) to run walk-forward CV per line, or a specific model
      registry name (e.g. 'ets', 'arima', 'prophet') to pin for ALL line
      items. Validated against the live registry only when the preset is
      used, not at creation time — registry membership depends on feature
      flags that can change later.
  - name: candidate_models
    type: array
    description: >
      Restrict the auto-selection candidate pool. Only meaningful when
      model_type='auto'; rejected if model_type is a pinned model.
  - name: default_horizon_months
    type: integer
    description: Default forecast horizon (months) when this preset is used and no horizon is given explicitly.
---

# Manage Model Presets

## When to Use
- User wants to save a model configuration for reuse: "create a model preset called Conservative using ETS"
- User wants to rename/adjust an existing preset
- User wants to retire a preset they no longer use

## Behavior
1. **create** — new `ModelPreset` row. Name must be unique (case-insensitive).
2. **update** — edit an existing preset's fields.
3. **deactivate** — soft-delete (sets `is_active=False`); never a hard delete,
   so forecast versions previously generated with it keep a valid reference.

After creating a preset, tell the user they can run a forecast with it via
`generate_baseline(model_preset="<name>")` or the Run Forecast panel.

## Examples
- "Create a model preset called Conservative using ETS" → action=create, name="Conservative", model_type="ets"
- "Make a preset that only tries ETS and ARIMA" → action=create, model_type="auto", candidate_models=["ets", "arima"]
- "Retire the Conservative preset" → action=deactivate
