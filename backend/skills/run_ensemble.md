---
name: run_ensemble
description: >
  Run an ensemble of multiple statistical models for a line item or set of line
  items, then combine predictions using weighted averaging based on each model's
  out-of-sample accuracy. Produces more robust forecasts than any single model.
required_role: generate
version: "1.0"
tags: [forecasting, ensemble, advanced, models]
parameters:
  - name: version_id
    type: string
    description: Forecast version to ensemble (uses active version if not specified)
  - name: line_item_name
    type: string
    description: "Specific line item to ensemble (if blank, runs for all low/medium confidence lines)"
  - name: models
    type: array
    description: "Models to include in ensemble (default: all available)"
    items:
      type: string
  - name: weighting_method
    type: string
    description: "How to weight model predictions: inverse_mape (default), equal, or rank"
    enum: [inverse_mape, equal, rank]
    default: inverse_mape
  - name: top_k
    type: integer
    description: Only use top K models by accuracy (default 3)
    default: 3
---

# Run Ensemble

## When to Use
Use this skill when:
- A line item has medium or low confidence and needs a better forecast
- The user wants a more robust prediction using multiple models
- Model selection is uncertain and combining models may help
- The user explicitly asks for an ensemble or blended forecast

## Behavior
1. Identifies target line items (specific item or all low/medium confidence)
2. For each target, runs all available models (ARIMA, Prophet, ETS, Linear)
3. Scores each model by out-of-sample MAPE using walk-forward validation
4. Selects the top K models
5. Combines predictions using the chosen weighting method:
   - **inverse_mape**: Weight ∝ 1/MAPE (better models get more weight)
   - **equal**: Equal weight to all models
   - **rank**: Weight ∝ rank position
6. Updates the ForecastLineResult with ensemble values
7. Recalculates confidence scores (ensembles typically score higher)

### When Ensemble Helps Most
- Volatile or seasonal data where different models capture different patterns
- Line items where no single model clearly dominates
- High-value items where accuracy improvement is worth the extra computation

## Examples
- "Run an ensemble for revenue" → line_item_name=revenue
- "Improve the low confidence forecasts with ensemble" → runs for all low confidence
- "Blend all models for the marketing budget" → line_item_name=marketing
- "Run ensemble with equal weighting" → weighting_method=equal
