---
name: manage_actuals_dataset
description: >
  Unpin the currently pinned (manually uploaded) actuals dataset so the next
  scheduled/API pull is free to become the dataset generate_baseline/
  plan_forecast use, or permanently delete a dataset (records + uploaded
  file). Admin only.
required_role: admin
version: "1.1"
tags: [actuals, ingestion, data, admin]
parameters:
  - name: action
    type: string
    description: "Action: unpin or delete"
    required: true
    enum: [unpin, delete]
  - name: dataset_id
    type: string
    description: >
      For unpin: dataset to act on, omit to target whichever pinned dataset
      currently resolves as current. Required for delete.
---

# Manage Actuals Dataset

## When to Use
- User says something like "stop using my manual upload" or "let the
  scheduled sync take over again" → unpin
- User says "delete that upload" / "remove this dataset" → delete
- An analyst manually uploaded a dataset for one-off testing and the team
  wants automated ingestion to resume driving forecasts

## Behavior
Every manual upload (via `ingest_actuals`, the chat paperclip / upload
endpoint) is pinned as authoritative — it always wins over any scheduled or
API pull ingested afterward, so a fresh manual upload immediately drives the
next forecast and an unattended nightly sync can never silently override it.

**unpin** clears that flag on the currently-pinned dataset (or a named one),
so `resolve_current_dataset` falls back to plain "latest ingested overall" —
letting the next scheduled/API pull become current again. It does not delete
any data; unpinned datasets remain queryable and re-ingesting the same
content manually will re-pin it.

**delete** permanently removes the dataset row, its actuals records, and the
uploaded file from disk. Refused if any forecast version was built from it
(`actuals_dataset_id` references it) — delete those versions first. Both
actions are refused for a dataset belonging to a different company than the
caller's.

## Examples
- "Let the ERP sync take over again" → action=unpin
- "Unpin dataset abc-123" → action=unpin, dataset_id="abc-123"
- "Delete dataset abc-123" → action=delete, dataset_id="abc-123"
