---
name: review_forecast
description: >
  Review and manage the forecast approval workflow with AI-powered triage.
  The AI analyzes all forecast items, identifies which can be auto-approved vs
  which need human attention, and provides specific findings for each item.
  Supports submit, approve, reject, and interactive review dashboard.
required_role: generate
version: "2.0"
tags: [review, approval, workflow, rbac, ai-triage]
parameters:
  - name: action
    type: string
    description: "Action: ai_triage, submit_for_review, approve, reject, review_queue, auto_approve_check"
    required: true
    enum: [ai_triage, submit_for_review, approve, reject, review_queue, auto_approve_check]
  - name: version_id
    type: string
    description: Forecast version ID (uses active version if not specified)
  - name: comments
    type: string
    description: Review comments (required for rejection)
  - name: publish
    type: boolean
    description: Also publish the forecast after approval
    default: false
---

# Review Forecast

## When to Use
Use this skill when the user wants to:
- **Review a forecast** → ALWAYS use `ai_triage` first — it analyzes items and opens the interactive dashboard
- Submit a forecast for review/approval
- Approve or reject a forecast (manager/admin only)
- View items needing attention
- Check if auto-approval is possible

## AI Triage (Primary Action)
When the user says "review the forecast" or "what needs review", use `ai_triage`.
This action:
1. Analyzes every line item using statistical heuristics (confidence, MAPE, band width, outliers)
2. Classifies items into: auto-approve / needs review / flagged
3. Shows a concise executive summary with only the items that matter
4. Opens the interactive Review Dashboard where the user can approve/reject individual items

## Behavior
### Workflow States
```
draft → in_review → approved → published
         ↓ (reject)
        draft (back to editing)
```

### RBAC Rules
- **ai_triage / review_queue**: All authenticated users
- **submit_for_review**: Any user with `generate` role
- **approve/reject**: Only `manager` or `admin` roles

### AI Analysis Checks
Each line item is evaluated on:
- Confidence score thresholds
- Prediction interval width (narrow = good)
- Model MAPE (lower = better)
- Deviation from actuals patterns
- Override impact magnitude
- Category outlier detection (z-score)

## Examples
- "Review the forecast" → action=ai_triage
- "What needs attention?" → action=ai_triage
- "Submit for review" → action=submit_for_review
- "Approve the forecast" → action=approve
- "Reject with comments" → action=reject, comments=...
- "Can this be auto-approved?" → action=auto_approve_check
