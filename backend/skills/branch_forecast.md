---
name: branch_forecast
description: >
  Create scenario branches from an existing forecast version. Supports
  what-if analysis by creating independent copies that can be modified
  without affecting the original. Can also merge branches back or compare
  branch vs. base.
required_role: generate
version: "1.0"
tags: [scenarios, branching, what-if, planning]
parameters:
  - name: action
    type: string
    description: "Action: create_branch, list_branches, compare_to_base, merge_to_base"
    required: true
    enum: [create_branch, list_branches, compare_to_base, merge_to_base]
  - name: source_version_id
    type: string
    description: Source version to branch from (uses active version if not specified)
  - name: branch_name
    type: string
    description: "Name for the new branch (e.g., 'Optimistic Q3', 'Downturn Scenario')"
  - name: branch_version_id
    type: string
    description: Branch version ID (for compare or merge actions)
  - name: adjustments
    type: object
    description: "Optional bulk adjustments to apply: {category: pct_change} e.g. {'Revenue': 10, 'COGS': -5}"
  - name: description
    type: string
    description: Description of the scenario this branch represents
---

# Branch Forecast (Scenarios)

## When to Use
Use this skill when the user wants to:
- Create a what-if scenario (e.g., "What if revenue grows 10%?")
- Branch the forecast for scenario planning
- Compare scenarios side-by-side
- Merge an approved scenario back into the main forecast

## Behavior
### create_branch
1. Deep-copies all ForecastLineResults from the source version
2. Creates a new ForecastVersion with type="scenario" and parent link
3. Optionally applies bulk percentage adjustments by category
4. The branch is independent — edits don't affect the source

### list_branches
Shows all branches of a given version with their status and modification counts

### compare_to_base
Runs variance analysis between a branch and its parent version

### merge_to_base
Promotes a branch's values back into the base version (creates new version)

### Scenario Types
- **Optimistic**: Revenue +10%, costs stable
- **Pessimistic**: Revenue -15%, costs +5%
- **Custom**: User-defined adjustments per category

## Examples
- "Create an optimistic scenario with 10% revenue growth" → action=create_branch, adjustments={"Revenue": 10}
- "What if COGS increases by 5%?" → action=create_branch, adjustments={"COGS": 5}
- "Show all scenarios" → action=list_branches
- "Compare the downturn scenario to base" → action=compare_to_base
