---
name: fetch_url
description: >
  Fetch and read content from any URL. Extracts readable text from web pages
  and optionally indexes the content in the context engine for future semantic
  search. Use when users paste links they want the agent to read and remember.
required_role: null
version: "1.0"
tags: [url, web, fetch, ingest, context]
parameters:
  - name: url
    type: string
    description: The URL to fetch and read
    required: true
  - name: save_to_context
    type: boolean
    description: Whether to index the content for future searches (default true)
    default: true
  - name: scope
    type: string
    description: "Scope for saved content: conversation or user"
    default: conversation
---

# Fetch URL

## When to Use
Use this skill when the user:
- Pastes a link and asks you to read or summarize it
- Wants to index a specific article or report for reference
- Says "Read this page" or "What does this article say?"
- Wants to save web content for future questions

## Behavior
- Fetches the URL and extracts readable text (strips navigation, scripts, ads)
- Shows a preview of the content in chat
- By default, indexes the content in the context engine so it can be found via search_context later
- Can be set to read-only (save_to_context=false) for one-time reads

## Examples
- "Read this article: https://example.com/report" → url="https://example.com/report"
- "Save this page for reference: https://sec.gov/filing" → url="https://sec.gov/filing", save_to_context=true
- "Just read this, don't save it: https://news.com/article" → url="https://news.com/article", save_to_context=false
