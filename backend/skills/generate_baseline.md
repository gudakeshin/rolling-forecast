---
name: generate_baseline
description: >
  Generate a statistical baseline forecast for all P&L line items using the
  best-fit model per line item. Auto-selection runs walk-forward cross-validation
  across registered models (including naive / seasonal-naive benchmarks when
  enabled) and picks by MASE → pinball → complexity (or legacy MAPE, depending
  on selection_metric). Produces point forecasts with confidence intervals
  (P10/P50/P90). Includes automatic confidence scoring and AI remediation.
  ALWAYS use model_type='auto' unless the user explicitly requests a specific model.
required_role: generate
version: "2.1"
tags: [forecasting, generation, statistical, baseline, multi-model]
parameters:
  - name: dataset_id
    type: string
    description: ID of the actuals dataset to use (uses latest if not specified)
    required: false
  - name: horizon_months
    type: integer
    description: Number of months to forecast forward (default 12)
    default: 12
  - name: model_type
    type: string
    description: >
      ALWAYS use 'auto' (default) which tests registered algorithms and picks the
      best per line item. Only set to a specific registry model name if the user
      EXPLICITLY requests it (e.g., "use ARIMA for everything"). Validated at
      execute time against the live model registry — not a fixed enum.
    default: auto
  - name: models_to_test
    type: array
    description: >
      Subset of registry model names to test during auto-selection. If omitted,
      all registered models are considered (subject to the two-stage cost screen).
      Use when the user wants to limit comparison scope (e.g., ["ets", "arima"]).
    required: false
  - name: random_seed
    type: integer
    description: Random seed for reproducibility (default 42)
    default: 42
---

# Generate Baseline Forecast

## IMPORTANT: Use plan_forecast first!
Before calling this skill, ALWAYS call `plan_forecast` to analyze the data and show
model comparison results to the user. Only proceed to generate_baseline after the
user has seen the MAPE comparison and confirmed their preferences.

## When to Use
Use this skill AFTER plan_forecast when:
- User has reviewed the model comparison and wants to proceed
- User explicitly says "run it" or "generate" after seeing the plan
- User asks for a specific model by name (skip planning in this case)

## Behavior
1. Retrieves the actuals dataset (latest by default)
2. For each non-calculated line item:
   - Analyzes data quality (sparse, zeros, stale, structural breaks)
   - **When model_type='auto':** Runs walk-forward CV on ALL eligible algorithms, picks the one with lowest MAPE
   - Stores the MAPE comparison results for each line item
   - Generates point forecast (P50) and confidence intervals (P10, P90)
   - Auto-scores confidence (0-100) and generates AI remediation recommendations
3. Creates a versioned ForecastVersion snapshot
4. Reports which model won for EACH line item, with MAPE scores

## Model Selection (auto mode)
All 4 algorithms are tested per line item via walk-forward cross-validation:
- **ETS (Exponential Smoothing)** — Often best for seasonal data
- **ARIMA/SARIMA** — Best for stationary data with autocorrelation
- **Prophet** — Handles trend changes and seasonality
- **Linear Trend** — Simple baseline, used when others fail

The response includes a MAPE comparison table showing how each model scored per line item.

## Key Parameters
- `model_type="auto"` → ALWAYS use this default. Tests all models, picks best per item.
- `models_to_test=["ets", "arima"]` → Only test these specific models.
- `model_type="arima"` → Force ARIMA for ALL items (only if user requests).

## Examples
- User confirmed auto-selection → `model_type="auto"` (default)
- "Only use ETS and ARIMA" → `model_type="auto"`, `models_to_test=["ets", "arima"]`
- "Use Prophet for everything" → `model_type="prophet"`
