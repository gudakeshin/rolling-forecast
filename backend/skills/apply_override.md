---
name: apply_override
description: >
  Apply a manual override to a forecast line item value. Requires a reason
  (minimum 10 characters). Automatically recalculates all downstream dependent
  line items using the P&L dependency graph. Can also revert a previous override.
required_role: override
version: "1.0"
tags: [override, adjustment, manual, dag]
parameters:
  - name: action
    type: string
    description: "Action to perform: apply (new override), revert (undo), or list (show overrides)"
    enum: [apply, revert, list]
    default: apply
  - name: line_item_name
    type: string
    description: Name or account code of the line item to override
  - name: period
    type: string
    description: "Period to override (e.g., '2026-03'). Use 'all' for all forecast periods."
  - name: new_value
    type: number
    description: The override value to set
  - name: reason
    type: string
    description: Reason for the override (minimum 10 characters, required for audit trail)
  - name: carry_forward
    type: boolean
    description: Whether this override should persist to the next forecast cycle
    default: true
  - name: version_id
    type: string
    description: Forecast version ID (uses active version if not specified)
  - name: override_id
    type: string
    description: Override ID (for revert action)
---

# Apply Override

## When to Use
Use this skill when the user wants to:
- Manually adjust a forecast value
- Override the statistical model's output
- Revert a previous override
- List all active overrides

## Behavior
### Apply Override
1. Validates the line item exists and the version is editable (draft or in_review)
2. **EC6**: Validates the override value for impossible/unusual values (soft warnings)
3. **EC10**: Checks for concurrent edits by other users on the same line
4. Creates an Override record with full audit trail (reason, user, timestamp)
5. Updates the ForecastLineResult with the override value
6. **EC5**: Recalculates all downstream dependents via the P&L dependency DAG

### Revert Override
1. Finds the override by ID or by line item name
2. Restores the original model value
3. Recalculates downstream dependents

### Override Validation Rules
- Reason must be at least 10 characters (SOX compliance)
- Only draft and in_review versions can be modified
- Negative values on non-negative lines generate warnings
- Changes > 100% from model value generate warnings
- Revenue overrides should be positive (soft warning if negative)

## Examples
- "Set Q2 marketing to $500K" → action=apply, line_item_name=marketing, new_value=500000
- "Override revenue for March to $1.2M" → action=apply
- "Revert the salary override" → action=revert, line_item_name=salary
- "Show all overrides" → action=list
