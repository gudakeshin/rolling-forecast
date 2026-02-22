---
name: detect_anomalies
description: >
  Detect anomalies and outliers in forecast data and actuals. Uses statistical
  methods (Z-score, IQR, trend deviation) to flag unusual values that may
  indicate data errors, structural changes, or genuine business events requiring
  attention.
required_role: null
version: "1.0"
tags: [anomaly, detection, outlier, data-quality]
parameters:
  - name: target
    type: string
    description: "What to scan: actuals (historical data), forecast (predicted values), or both"
    enum: [actuals, forecast, both]
    default: both
  - name: version_id
    type: string
    description: Forecast version to analyze (uses active version if not specified)
  - name: method
    type: string
    description: "Detection method: zscore (default), iqr, trend_deviation, all"
    enum: [zscore, iqr, trend_deviation, all]
    default: all
  - name: sensitivity
    type: string
    description: "Detection sensitivity: low (fewer flags), medium, high (more flags)"
    enum: [low, medium, high]
    default: medium
  - name: category
    type: string
    description: "Filter to a specific category (e.g., 'Revenue')"
  - name: line_item_name
    type: string
    description: "Filter to a specific line item"
---

# Detect Anomalies

## When to Use
Use this skill when the user wants to:
- Check for data quality issues in actuals or forecasts
- Identify unusual spikes or drops in financial data
- Find potential data entry errors
- Detect structural changes that may affect forecast quality

## Behavior
1. Loads actuals and/or forecast data for all line items (or filtered subset)
2. For each line item's time series, applies anomaly detection:
   - **Z-score**: Flags points > N standard deviations from mean
   - **IQR**: Flags points outside 1.5× interquartile range
   - **Trend deviation**: Flags points that deviate significantly from the trend line
3. Classifies anomalies by severity (critical, warning, info)
4. Groups results by category and line item
5. Suggests potential causes and recommended actions

### Sensitivity Thresholds
| Sensitivity | Z-score Threshold | IQR Multiplier |
|------------|------------------|----------------|
| Low        | 3.0 σ            | 2.0×           |
| Medium     | 2.5 σ            | 1.5×           |
| High       | 2.0 σ            | 1.0×           |

### Anomaly Classifications
- **Critical**: Very large deviation, likely data error or major event
- **Warning**: Notable deviation, review recommended
- **Info**: Mild deviation, may be normal variation

## Examples
- "Check for anomalies in the actuals data" → target=actuals
- "Any outliers in the forecast?" → target=forecast
- "Scan revenue for unusual patterns" → category=Revenue
- "Run a thorough anomaly check" → sensitivity=high, method=all
