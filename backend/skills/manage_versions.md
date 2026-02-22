---
name: manage_versions
description: >
  List, load, and manage forecast versions. Can list all versions, show details
  of a specific version, set the active version, or update a version's status
  (draft, in_review, approved, published).
required_role: null
version: "1.0"
tags: [versioning, management, workflow]
parameters:
  - name: action
    type: string
    description: "Action to perform"
    required: true
    enum: [list, get, set_active, update_status]
  - name: version_id
    type: string
    description: Version ID for get/set_active/update_status actions
    required: false
  - name: status
    type: string
    description: New status for update_status action
    enum: [draft, in_review, approved, published, archived]
---

# Manage Versions

## When to Use
Use this skill when the user:
- Asks about forecast versions or history
- Wants to see what versions exist
- Wants to change the active forecast
- Needs to update a version's workflow status

## Behavior
- **list**: Shows all versions with status, line counts, confidence breakdown
- **get**: Detailed view of a specific version with all metadata
- **set_active**: Changes which version is the working context
- **update_status**: Transitions a version through the workflow (draft → in_review → approved → published)

The active version (marked with *) is used by default in all other skills.

## Examples
- "Show me all forecast versions" → action=list
- "What versions do we have?" → action=list
- "Switch to the previous forecast" → action=set_active
- "Publish the forecast" → action=update_status, status=published
