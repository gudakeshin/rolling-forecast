---
name: collect_driver_input
description: >
  Manage business unit driver assumption inputs. Can create input forms, show
  pending forms for a BU, submit driver values, or check submission status.
  Driver inputs represent business assumptions that feed into the forecast.
required_role: override
version: "1.0"
tags: [drivers, assumptions, input, business-unit]
parameters:
  - name: action
    type: string
    description: "Action: create_form, list_forms, show_form, submit_values, check_status"
    required: true
    enum: [create_form, list_forms, show_form, submit_values, check_status]
  - name: business_unit
    type: string
    description: "Business unit name (e.g., 'North America', 'EMEA')"
  - name: form_id
    type: integer
    description: Form config ID (for show_form or submit_values)
  - name: values
    type: object
    description: "Driver values to submit: {field_name: {value, reason}}"
  - name: version_id
    type: string
    description: Forecast version ID (uses active version if not specified)
  - name: form_name
    type: string
    description: Name for a new form (for create_form action)
  - name: fields
    type: array
    description: "Field definitions for create_form: [{name, label, type, line_item_name}]"
    items:
      type: object
---

# Collect Driver Input

## When to Use
Use this skill when the user wants to:
- Submit business unit assumptions or driver inputs
- Create a new driver input form for a BU
- Check which BUs have submitted their assumptions
- View pending input forms

## Behavior
1. **create_form**: Define a form with fields linked to forecast line items
2. **list_forms**: Show all active forms, optionally filtered by BU
3. **show_form**: Display a form with model-suggested values for comparison
4. **submit_values**: Submit driver values and automatically apply as overrides
5. **check_status**: Dashboard of submission status across all BUs

### Edge Cases
- **EC7 (Late Submissions)**: Warns if submission is past soft deadline; marks as "late" if past hard deadline

## Examples
- "Submit my assumptions for North America" → action=submit_values
- "Which BUs still haven't submitted?" → action=check_status
- "Create a driver form for EMEA" → action=create_form
- "Show the input form for Asia Pacific" → action=show_form
