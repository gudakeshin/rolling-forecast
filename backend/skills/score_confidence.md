---
name: score_confidence
description: >
  Score each forecast line item by model confidence on a 0-100 scale.
  Categorizes lines as High Confidence (auto-approvable), Medium Confidence
  (review recommended), or Low Confidence (review required).
required_role: null
version: "1.0"
tags: [forecasting, confidence, scoring, review]
parameters:
  - name: version_id
    type: string
    description: Forecast version ID to score (uses active version if not specified)
    required: false
  - name: threshold_low
    type: integer
    description: Score below this = Low Confidence (default 50)
    default: 50
  - name: threshold_medium
    type: integer
    description: Score below this = Medium Confidence (default 70)
    default: 70
---

# Score Confidence

## When to Use
Use this skill after generating a baseline forecast to:
- Score and categorize forecast line items by confidence
- Identify lines needing manual review
- Determine which lines can be auto-approved
- Generate the review queue

## Behavior
Computes a composite confidence score (0-100) blending:
- **Model MAPE** (40% weight) — lower error = higher confidence
- **Prediction Interval Width** (25% weight) — narrower intervals = higher confidence
- **R-squared** (20% weight) — better fit = higher confidence
- **Model Complexity Base** (15% weight) — complex models get higher base score

### Confidence Levels
| Level  | Score Range | Action |
|--------|------------|--------|
| High   | 70+        | Auto-approvable |
| Medium | 50-70      | Review recommended |
| Low    | < 50       | Review required |

## Examples
- "Score the confidence levels" → triggers this skill
- "Which lines need review?" → triggers this skill
- "What's the confidence distribution?" → triggers this skill
