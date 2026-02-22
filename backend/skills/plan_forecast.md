---
name: plan_forecast
description: >
  Analyze uploaded actuals data and plan the forecast configuration before running.
  Runs all 4 algorithms (ARIMA, Prophet, ETS, Linear) on representative line items,
  computes MAPE scores via walk-forward cross-validation, and presents results so
  the user can make informed choices about models, horizon, and configuration.
  This is the FIRST step in the forecasting workflow.
required_role: generate
version: "1.0"
tags: [forecasting, planning, analysis, model-comparison]
parameters:
  - name: dataset_id
    type: string
    description: ID of the actuals dataset to analyze (uses latest if not specified)
    required: false
  - name: sample_size
    type: integer
    description: Number of line items to sample for model comparison (default 5, max 10)
    default: 5
---

# Plan Forecast

## When to Use
ALWAYS use this skill as the FIRST step when the user wants to:
- Generate a new forecast
- Run forecasting models
- Create a baseline forecast
- Compare algorithm performance
- Understand which model fits their data best

## Behavior
1. Analyzes data quality across all line items:
   - History length per item
   - Zero-activity detection
   - Volatility analysis (coefficient of variation)
   - Category breakdown

2. Runs model comparison on a representative sample:
   - Tests ALL 4 algorithms: **ARIMA**, **Prophet**, **ETS**, **Linear Trend**
   - Uses walk-forward cross-validation with 6-month holdout
   - Computes **MAPE** (Mean Absolute Percentage Error) for each model on each item
   - Identifies the best model per line item

3. Presents results to the user:
   - Data overview table
   - MAPE comparison table (model × line item)
   - Algorithm performance summary
   - AI recommendation

4. Asks the user to choose:
   - Auto-selection (recommended) vs. forced model
   - Which algorithms to include/exclude
   - Forecast horizon
   - Any other configuration

## Workflow
```
User: "Generate a forecast"
→ Agent calls plan_forecast (this skill)
→ Shows model comparison results
→ Asks: "Which approach do you prefer?"
→ User: "Use auto-selection with 12 months"
→ Agent calls generate_baseline with model_type="auto", horizon_months=12
```

## Examples
- "Generate a forecast" → call plan_forecast FIRST, then generate_baseline
- "Which model should I use?" → call plan_forecast
- "Compare ARIMA vs ETS" → call plan_forecast
- "Run a 6-month forecast using Prophet" → call generate_baseline directly (user already chose)
