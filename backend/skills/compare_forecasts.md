---
name: compare_forecasts
description: >
  Compare two forecast versions against each other or compare a forecast against
  historical actuals. Performs variance analysis showing absolute and percentage
  differences by line item and period.
required_role: null
version: "1.0"
tags: [comparison, variance, analysis]
parameters:
  - name: comparison_type
    type: string
    description: "Type of comparison: version_vs_version or forecast_vs_actuals"
    enum: [version_vs_version, forecast_vs_actuals]
    default: version_vs_version
  - name: version_id_a
    type: string
    description: First version ID (current/active version if not specified)
  - name: version_id_b
    type: string
    description: Second version ID (for version_vs_version comparison)
  - name: category_filter
    type: string
    description: "Filter by P&L category (e.g., 'Revenue', 'COGS', 'OpEx')"
  - name: threshold_pct
    type: number
    description: Only show lines with variance above this threshold (%). Default 0 = show all.
    default: 0
  - name: top_n
    type: integer
    description: Show only top N variances by absolute percentage. Default 20.
    default: 20
---

# Compare Forecasts

## When to Use
Use this skill when the user wants to:
- Compare two forecast versions (e.g., current vs. previous)
- Check what changed between forecast cycles
- Compare forecast accuracy against historical actuals
- Find the biggest variances

## Behavior
### Version vs Version
- Aggregates P50 values by line item for both versions
- Computes absolute and percentage variances
- Sorts by largest absolute % change
- Provides category-level summaries

### Forecast vs Actuals
- Compares forecast P50 against actual historical values
- Calculates forecast accuracy (100% - |variance %|)
- Highlights biggest misses

## Examples
- "Compare the current forecast to the previous one" → comparison_type=version_vs_version
- "What changed since last month?" → comparison_type=version_vs_version
- "How accurate is the forecast?" → comparison_type=forecast_vs_actuals
- "Show revenue variances above 5%" → category_filter=Revenue, threshold_pct=5
