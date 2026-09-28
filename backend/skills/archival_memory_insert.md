---
name: archival_memory_insert
description: >
  Store a durable note in archival memory so it can be recalled semantically in later
  conversations, without consuming the always-in-prompt core memory budget.
required_role: generate
version: "1.0"
tags: [memory, persistence, archival]
parameters:
  - name: text
    type: string
    description: Note to archive
    required: true
  - name: provenance
    type: string
    description: Where the note came from
    enum: [agent, document, reflection]
    default: agent
  - name: business_unit
    type: string
    description: Business unit scope; defaults to the caller's business unit
---

# Archival Memory Insert

## When to Use
- A detail is worth keeping but is too long or too specific for core memory.
- A conclusion from analysis, a document, or a reflection pass should survive the session.

## Behavior
1. Validate `provenance` (agent, document, or reflection).
2. Scope the entry to the caller's business unit unless the caller may write across BUs.
3. Embed and store the note in the archival vector collection.
4. Emit a `memory.archival.insert` audit event.

## Examples
- "Remember that EMEA closes two days later than NA"
- "Archive this reflection about recurring revenue over-forecasting"
