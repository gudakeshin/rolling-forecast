---
name: conversation_search
description: >
  Search prior conversation messages to recover earlier decisions, assumptions, and discussion context.
required_role: input
version: "1.0"
tags: [memory, recall, search]
parameters:
  - name: query
    type: string
    description: Search phrase
    required: true
  - name: limit
    type: integer
    description: Maximum number of hits
---

# Conversation Search

## When to Use
- The user asks "what did we decide before?"
- You need prior context from older chats.

## Behavior
1. Search within the caller's own conversations.
2. Return recent matching snippets with timestamps.
