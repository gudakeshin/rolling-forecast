---
name: auto_accuracy_report
description: >
  Generate an automated forecast accuracy report comparing past forecasts against
  realized actuals. Tracks MAPE, bias, and accuracy trends over time. Identifies
  systematically over- or under-forecasted items.
required_role: null
version: "1.0"
tags: [accuracy, reporting, backtesting, analysis]
parameters:
  - name: version_id
    type: string
    description: Forecast version to analyze (uses active version if not specified)
  - name: report_type
    type: string
    description: "Report type: accuracy_summary, bias_analysis, trend_over_time, line_item_detail"
    enum: [accuracy_summary, bias_analysis, trend_over_time, line_item_detail]
    default: accuracy_summary
  - name: category
    type: string
    description: "Filter by category (e.g., 'Revenue')"
  - name: line_item_name
    type: string
    description: Specific line item for detailed analysis
  - name: top_n
    type: integer
    description: Show top N most/least accurate items
    default: 10
---

# Auto Accuracy Report

## When to Use
Use this skill when the user wants to:
- Check how accurate past forecasts were
- Identify systematic bias in the forecasting models
- Find items that are consistently over- or under-forecasted
- Generate accuracy metrics for management reporting

## Behavior
### accuracy_summary
- Compares each forecasted value against actual realized values
- Computes MAPE, mean bias, and hit rate (% within ±10%)
- Ranks by accuracy with category breakdowns

### bias_analysis
- Identifies directional bias: consistently too high or too low
- Flags items with persistent positive/negative forecast error
- Recommends bias corrections

### trend_over_time
- Shows how forecast accuracy has evolved across versions
- Tracks whether the models are improving with more data

### line_item_detail
- Deep dive on a specific line item's forecast accuracy history
- Shows actuals vs forecast for each period
- Model-level accuracy breakdown

### Key Metrics
| Metric | Description |
|--------|------------|
| MAPE | Mean Absolute Percentage Error |
| Bias | Average signed error (positive = over-forecast) |
| Hit Rate | % of periods within ±10% of actuals |
| Tracking Signal | Cumulative bias ÷ MAD (flags systematic drift) |

## Examples
- "How accurate is the forecast?" → report_type=accuracy_summary
- "Are we consistently over-forecasting revenue?" → report_type=bias_analysis, category=Revenue
- "Is the model getting better over time?" → report_type=trend_over_time
- "Detail accuracy for the salary line" → report_type=line_item_detail, line_item_name=salary
