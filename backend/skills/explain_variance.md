---
name: explain_variance
description: >
  Explain why a forecast line moved using honest variance attribution
  (Q×P/mix identity when drivers exist, else FX, else unattributed override text).
  Opens the Explain Variance panel with a bridge waterfall.
required_role: generate
version: "1.0"
tags: [variance, attribution, bridge, explain]
parameters:
  - name: version_id
    type: string
    description: Forecast version (uses active if omitted)
  - name: line_item_name
    type: string
    description: Line item name or account code
  - name: line_item_id
    type: integer
    description: Line item id
  - name: period_from
    type: string
    description: Start period for attribution
  - name: period_to
    type: string
    description: End period for attribution
  - name: basis
    type: string
    description: auto|identity|fx|override
    enum: [auto, identity, fx, override]
  - name: convention
    type: string
    description: volume_first|price_first
    enum: [volume_first, price_first]
  - name: open_panel
    type: boolean
    description: Open explainability panel
---

# Explain Variance

## When to Use
- "Why did revenue move?"
- "Decompose the miss vs prior"
- "Show the variance bridge / waterfall"

## Behavior
Runs the attribution ladder (identity Q×P → FX → override text), returns
bucket breakdown + bridge chart, and opens the Explain Variance panel.

## Examples
- "Why did Product Revenue change?" → line_item_name=Product Revenue
- "Explain the bridge for this version" → open panel without a line
- "Decompose volume vs price for SKU A from 2025-01 to 2025-06"
