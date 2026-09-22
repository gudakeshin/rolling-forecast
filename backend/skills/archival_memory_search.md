---
name: archival_memory_search
description: >
  Semantic search over archival memory to recall durable facts, prior decisions, and past
  reflection findings. Results never cross business-unit boundaries.
required_role: input
version: "1.0"
tags: [memory, recall, archival]
parameters:
  - name: query
    type: string
    description: What to recall
    required: true
  - name: top_k
    type: integer
    description: Maximum number of hits
    default: 5
---

# Archival Memory Search

## When to Use
- The user references something established earlier that is not in core memory.
- Before re-deriving a conclusion that a previous session may already have recorded.

## Behavior
1. Embed the query and retrieve the nearest archival entries.
2. Restrict hits to the caller's business unit plus shared (unscoped) entries.
3. Emit a `memory.archival.search` audit event.

## Examples
- "What did we conclude about the EMEA close calendar?"
- "Recall prior findings on revenue forecast bias"
