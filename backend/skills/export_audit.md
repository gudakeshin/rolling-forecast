---
name: export_audit
description: >
  Generate a comprehensive audit trail export for a forecast version. Includes
  full lineage: data source, model parameters, override history, approval
  workflow, and version metadata. Suitable for SOX audit compliance.
required_role: null
version: "1.0"
tags: [audit, compliance, sox, export]
parameters:
  - name: version_id
    type: string
    description: Forecast version to audit (uses active version if not specified)
  - name: format
    type: string
    description: "Export format: summary (chat display), json (full structured), csv (tabular)"
    enum: [summary, json, csv]
    default: summary
  - name: include_model_details
    type: boolean
    description: Include detailed model parameters and diagnostics
    default: true
---

# Export Audit Trail

## When to Use
Use this skill when the user needs to:
- Generate an audit trail for SOX compliance
- Export forecast lineage and provenance
- Create a compliance report
- Document model parameters and override history

## Behavior
The audit trail includes:
1. **Version Metadata**: ID, name, status, creation/approval timestamps, users
2. **Data Lineage**: Source file, data hash, period range, completeness
3. **Model Configuration**: Model versions, generation time, selection method
4. **Model Details** (optional): Per-line model type, MAPE, R², training window, seeds
5. **Override History**: All overrides (active + reverted), users, reasons, downstream impacts

### Export Formats
- **summary**: Rich table display in chat
- **json**: Full structured JSON (downloadable for audit teams)
- **csv**: Tabular format focusing on model details

## Examples
- "Generate an audit trail" → format=summary
- "Export the full audit as JSON" → format=json
- "I need the compliance report for this forecast" → format=summary
- "Show the model audit details" → format=csv
