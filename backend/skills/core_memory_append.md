---
name: core_memory_append
description: >
  Append durable core memory so the assistant remembers stable organizational or user context across sessions.
required_role: generate
version: "1.0"
tags: [memory, persistence, context]
parameters:
  - name: scope
    type: string
    description: persona|organization|user|business_unit
    enum: [persona, organization, user, business_unit]
  - name: label
    type: string
    description: Short memory block label
    required: true
  - name: content
    type: string
    description: Text to append
    required: true
  - name: char_limit
    type: integer
    description: Maximum block size before replacement is required
---

# Core Memory Append

## When to Use
- The user states stable operating context worth persisting.
- A recurring preference should survive future conversations.

## Behavior
1. Resolve the scope owner (org/user/BU/persona).
2. Append content to an existing block or create one.
3. Enforce `char_limit`; if exceeded, advise `core_memory_replace`.
4. Emit an audit trail.
