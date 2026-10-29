---
name: get_custom_kpis
description: >
  Evaluate the company's custom KPIs -- admin-defined formulas over line
  items and categories such as gross margin % or opex ratio -- for a
  forecast version, as horizon totals.
required_role: input
version: "1.0"
tags: [kpi, reporting, executive]
parameters:
  - name: version_id
    type: string
    description: Forecast version (uses the active version if omitted)
---

# Get Custom KPIs

## When to Use
- User asks for a KPI their company defined: "what's our gross margin %?"
- User asks which KPIs are tracked, or for a KPI summary of a version

## Behavior
Read-only. Each KPI is evaluated on the version's horizon totals (a ratio KPI
is the ratio of totals). A KPI with a missing input or a division by zero has
no value; say so rather than guessing. KPIs are defined and edited by admins
in the Executive Dashboard, not through chat.

## Examples
- "What are our KPIs for this forecast?"
- "Show gross margin percent for the latest version"
