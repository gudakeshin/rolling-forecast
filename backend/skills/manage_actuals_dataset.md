---
name: manage_actuals_dataset
description: >
  Unpin the currently pinned (manually uploaded) actuals dataset so the next
  scheduled/API pull is free to become the dataset generate_baseline/
  plan_forecast use. Admin only.
required_role: admin
version: "1.0"
tags: [actuals, ingestion, data, admin]
parameters:
  - name: action
    type: string
    description: "Action: unpin"
    required: true
    enum: [unpin]
  - name: dataset_id
    type: string
    description: >
      Dataset to unpin. Omit to unpin whichever pinned dataset currently
      resolves as current.
---

# Manage Actuals Dataset

## When to Use
- User says something like "stop using my manual upload" or "let the
  scheduled sync take over again"
- An analyst manually uploaded a dataset for one-off testing and the team
  wants automated ingestion to resume driving forecasts

## Behavior
Every manual upload (via `ingest_actuals`, the chat paperclip / upload
endpoint) is pinned as authoritative — it always wins over any scheduled or
API pull ingested afterward, so a fresh manual upload immediately drives the
next forecast and an unattended nightly sync can never silently override it.
`unpin` clears that flag on the currently-pinned dataset (or a named one),
so `resolve_current_dataset` falls back to plain "latest ingested overall" —
letting the next scheduled/API pull become current again. It does not delete
any data; unpinned datasets remain queryable and re-ingesting the same
content manually will re-pin it.

## Examples
- "Let the ERP sync take over again" → action=unpin
- "Unpin dataset abc-123" → action=unpin, dataset_id="abc-123"
