---
name: search_context
description: >
  Search uploaded documents and indexed web content using semantic search (RAG).
  Returns the most relevant passages from the user's document library with
  source attribution and relevance scores.
required_role: null
version: "1.0"
tags: [context, search, rag, documents]
parameters:
  - name: query
    type: string
    description: The search query to find relevant content in uploaded documents
    required: true
  - name: top_k
    type: integer
    description: Number of results to return (default 5)
    default: 5
  - name: file_type
    type: string
    description: "Filter by file type (pdf, docx, xlsx, csv, url, etc.)"
  - name: scope
    type: string
    description: "Filter by scope (user, conversation, global)"
---

# Search Context

## When to Use
Use this skill when the user:
- Asks questions that could be answered by their uploaded documents
- Wants to find specific information in their document library
- Asks "What does [document] say about...?"
- Needs to reference internal reports, strategy decks, or board materials
- Asks about content from previously uploaded files or indexed URLs

## Behavior
- Performs semantic search across all documents the user has uploaded
- Returns ranked passages with relevance scores and source attribution
- Can filter by file type (PDF, DOCX, etc.) or scope
- Opens the Document Library panel for full document management

## Examples
- "What does our strategy deck say about growth targets?" → query="growth targets strategy", file_type=null
- "Find revenue projections in uploaded documents" → query="revenue projections"
- "Search my PDFs for cost reduction plans" → query="cost reduction plans", file_type="pdf"
- "What are the key findings from the board report?" → query="key findings board report"
