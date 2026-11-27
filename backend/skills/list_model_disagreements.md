---
name: list_model_disagreements
description: >
  List lines where the published forecast sits outside the Chronos-2
  foundation model's P10-P90 for most of the horizon ("Models disagree"),
  and how review has treated them so far.
required_role: null
version: "1.0"
tags: [review, second-opinion, foundation-model, risk]
parameters:
  - name: version_id
    type: string
    description: Forecast version (defaults to the active one)
  - name: limit
    type: integer
    description: Maximum lines to list
    default: 10
---

# List Model Disagreements

## When to Use
- "Where should I look first?" / "Which lines are risky?"
- "Does the foundation model agree with our forecast?"
- Before approving a version, to see flagged lines not yet reviewed

## Behavior
1. Find lines in the version carrying the "Models disagree" flag
2. List them by share of periods outside Chronos-2's range, with direction
3. Report how many flagged lines have been reviewed, and the share of all
   reviewed lines that were flagged ones
4. Open the review queue, which includes flagged lines whatever their confidence

The flag never changes a number; it is a prompt to look.

## Examples
- "Where do the models disagree?" → list_model_disagreements()
- "Top 5 disagreements in version V" → list_model_disagreements(version_id=V, limit=5)
