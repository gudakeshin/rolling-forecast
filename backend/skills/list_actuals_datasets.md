---
name: list_actuals_datasets
description: >
  List recently ingested actuals datasets (manual uploads and scheduled/API
  pulls), showing which one is pinned as authoritative and would be used by
  generate_baseline/plan_forecast. Read-only — any analyst can use this.
required_role: generate
version: "1.0"
tags: [actuals, ingestion, data]
parameters:
  - name: limit
    type: integer
    description: Max datasets to return, most recent first.
    default: 10
---

# List Actuals Datasets

## When to Use
- User asks what data is currently being used for forecasting
- User wants to understand why a forecast used a particular dataset
- User wants to check whether a scheduled/API sync landed and what happened to it

## Behavior
Lists recent `ActualsDataset` rows with source, ingestion time, and whether
each is `is_pinned` (manually uploaded — see `manage_actuals_dataset`). Also
resolves and surfaces which one is actually "current" (the one
generate_baseline/plan_forecast would use with no explicit dataset_id) via
the same `resolve_current_dataset` logic those skills use: the latest pinned
dataset if any exist, otherwise the latest dataset overall.

## Examples
- "What data is my forecast using?" → list, look at `current_dataset_id`
- "Did last night's sync come in?" → list, check the most recent `api`/`warehouse` row
