---
name: list_model_presets
description: >
  List saved model presets (named shortcuts for a model_type/models_to_test
  combination usable with generate_baseline's model_preset parameter), or
  look up one by name/id. Read-only — any analyst can use this.
required_role: generate
version: "1.0"
tags: [forecasting, models, presets]
parameters:
  - name: preset
    type: string
    description: Name or id of a specific preset to look up. Omit to list all.
    required: false
  - name: include_inactive
    type: boolean
    description: Include deactivated presets in the list.
    default: false
---

# List Model Presets

## When to Use
- User asks what model presets/configurations are available
- User wants details on a specific saved preset before running a forecast with it
- Before calling generate_baseline with a model_preset, to confirm it exists and is active

## Behavior
Reads saved `ModelPreset` rows. Does not validate against the live model
registry (that happens only when a preset is actually used to run a forecast) —
this is a plain read.

## Examples
- "What model presets do we have?" → list all
- "Tell me about the Conservative preset" → preset="Conservative"
