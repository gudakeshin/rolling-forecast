---
name: manage_drivers
description: >
  Manage causal driver series (headcount, volume, price, macro). List drivers with
  freshness, create drivers, assert links to line items, or open the Driver Series panel.
  Distinct from collect_driver_input (BU assumption forms).
required_role: manage_drivers
version: "1.0"
tags: [drivers, causal, links, freshness]
parameters:
  - name: action
    type: string
    description: "Action: list, create, assert_link, open_panel"
    required: true
    enum: [list, create, assert_link, open_panel]
  - name: key
    type: string
    description: Driver key for create
  - name: name
    type: string
    description: Display name for create
  - name: driver_type
    type: string
    description: volume|price|headcount|macro|index|other
  - name: driver_id
    type: integer
    description: Driver id for assert_link
  - name: line_item_id
    type: integer
    description: Line item id for assert_link
  - name: relation
    type: string
    description: level|quantity|unit_price|elasticity
  - name: lag
    type: integer
    description: Lag periods
  - name: coefficient
    type: number
    description: Optional coefficient / elasticity
---

# Manage Drivers

## When to Use
- User asks to list causal drivers or check freshness
- Create a driver series (headcount, volume, price, macro)
- Link a driver to a P&L line item
- Open the Driver Series management panel

## Behavior
1. **list** — scoped drivers with optional freshness
2. **create** — new driver row (unique key)
3. **assert_link** — candidate link from driver → line item
4. **open_panel** — trigger Driver Series side panel

## Examples
- "Show me our causal drivers" → action=list
- "Create a headcount driver for NA" → action=create
- "Link headcount to Payroll" → action=assert_link
- "Open driver management" → action=open_panel
