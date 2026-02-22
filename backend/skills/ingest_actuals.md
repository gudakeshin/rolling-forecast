---
name: ingest_actuals
description: >
  Upload and validate historical financial actuals data. Accepts a CSV or Excel
  file path, validates data completeness and freshness, creates or updates line
  items in the chart of accounts, and stores the data with full lineage tracking.
required_role: generate
version: "1.0"
tags: [data, ingestion, actuals]
parameters:
  - name: file_path
    type: string
    description: Path to the CSV or Excel file containing actuals data
    required: true
  - name: source_type
    type: string
    description: "Data source type: csv (default) or api"
    default: csv
    enum: [csv, api]
---

# Ingest Actuals

## When to Use
Use this skill when the user wants to:
- Upload or load financial actuals data
- Ingest historical P&L data for forecasting
- Refresh actuals from a new data file
- Load data from CSV or Excel files

## Behavior
1. Reads the specified CSV/Excel file using the adapter pattern
2. Validates data structure (account_code, period, value columns required)
3. Checks data completeness and identifies missing periods
4. Creates or updates LineItem records in the chart of accounts
5. Stores ActualsRecord entries with full lineage (file hash, timestamps)
6. Updates working memory with the latest dataset ID

### Data Format
The input CSV should contain at minimum:
- `account_code` - Unique identifier for each P&L line item
- `period` - Date period in YYYY-MM format
- `value` - Numerical value for the period

Optional columns: `account_name`, `category`, `business_unit`, `geography`, `product_line`, `currency`

### Validation
- Checks for missing periods and reports completeness percentage
- Generates a cryptographic hash for data provenance
- Warns about data quality issues

## Examples
- "Upload the actuals file" → triggers this skill
- "Load financial data from revenue_actuals.csv" → triggers this skill
- "Ingest the latest P&L data" → triggers this skill
- "Refresh the actuals data" → triggers this skill
