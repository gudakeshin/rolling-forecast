---
name: query_forecast
description: >
  Query forecast data to answer questions. Can retrieve specific line items,
  summarize categories, show overrides, get confidence details, or provide
  aggregate statistics.
required_role: null
version: "1.0"
tags: [query, analysis, exploration]
parameters:
  - name: query_type
    type: string
    description: "Type of query"
    required: true
    enum: [line_item, category_summary, confidence_summary, overrides, version_summary, search]
  - name: version_id
    type: string
    description: Version ID to query (uses active if not specified)
  - name: line_item_name
    type: string
    description: Line item name or account code to look up
  - name: category
    type: string
    description: "Category to filter by (e.g., Revenue, COGS, OpEx)"
  - name: search_term
    type: string
    description: Search term to find matching line items
  - name: confidence_level
    type: string
    description: Filter by confidence level
    enum: [high, medium, low]
---

# Query Forecast

## When to Use
Use this skill when the user asks questions about forecast data such as:
- "What is the revenue forecast?"
- "Which lines have low confidence?"
- "Show me the OpEx breakdown"
- "How many overrides are there?"
- "Search for marketing-related items"

## Behavior
- **version_summary**: High-level summary with category totals and average confidence
- **category_summary**: Drill into a specific P&L category (Revenue, COGS, OpEx, etc.)
- **line_item**: Detailed period-by-period view of a specific line item
- **confidence_summary**: Filter and list items by confidence level
- **overrides**: Show all active overrides in the version
- **search**: Full-text search across line item names, codes, and categories

## Examples
- "What's the revenue forecast?" → query_type=category_summary, category=Revenue
- "Show me low confidence items" → query_type=confidence_summary, confidence_level=low
- "Look up the marketing budget line" → query_type=line_item, line_item_name=marketing
- "Search for salary items" → query_type=search, search_term=salary
