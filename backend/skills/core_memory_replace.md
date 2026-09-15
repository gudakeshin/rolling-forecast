---
name: core_memory_replace
description: >
  Replace an existing memory block with a consolidated version when facts change or the block becomes noisy.
required_role: generate
version: "1.0"
tags: [memory, persistence, consolidation]
parameters:
  - name: scope
    type: string
    description: persona|organization|user|business_unit
    enum: [persona, organization, user, business_unit]
  - name: label
    type: string
    description: Memory block label
    required: true
  - name: content
    type: string
    description: Replacement content
    required: true
  - name: char_limit
    type: integer
    description: Maximum block size
---

# Core Memory Replace

## When to Use
- Existing memory is stale, contradictory, or too long.
- The user asks to overwrite a saved fact set.

## Behavior
1. Resolve target scope.
2. Replace content (not append).
3. Bump version and record audit event.
