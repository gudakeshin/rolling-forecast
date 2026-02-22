---
name: generate_commentary
description: >
  Generate AI-powered natural language commentary for forecast results. Uses the
  LLM to produce executive-ready narrative explaining key drivers, variances,
  risks, and recommendations. Can generate commentary for a full forecast version,
  specific categories, or individual line items.
required_role: null
version: "1.0"
tags: [commentary, narrative, ai, reporting]
parameters:
  - name: version_id
    type: string
    description: Forecast version to commentate (uses active version if not specified)
  - name: scope
    type: string
    description: "Scope of commentary: executive (high-level), category (by P&L section), line_item (detailed)"
    enum: [executive, category, line_item]
    default: executive
  - name: category
    type: string
    description: "Category to focus on (for category scope, e.g., 'Revenue', 'COGS')"
  - name: line_item_name
    type: string
    description: Specific line item (for line_item scope)
  - name: include_risks
    type: boolean
    description: Include risk assessment and flags
    default: true
  - name: include_recommendations
    type: boolean
    description: Include actionable recommendations
    default: true
  - name: tone
    type: string
    description: "Commentary tone: formal (board-ready), concise (bullet points), analytical (deep-dive)"
    enum: [formal, concise, analytical]
    default: formal
---

# Generate Commentary

## When to Use
Use this skill when the user wants to:
- Generate a narrative summary of the forecast
- Prepare executive commentary for a board review
- Understand key drivers and risk factors
- Get written analysis of forecast variances and trends

## Behavior
1. Gathers relevant forecast data (line items, confidence, overrides, variances)
2. Identifies key patterns:
   - Largest changes vs. prior period/version
   - Low confidence items and their drivers
   - Override patterns and justifications
   - Category-level trends
3. Constructs a prompt with the data context
4. Uses the LLM to generate structured commentary
5. Returns formatted narrative with sections

### Commentary Sections (Executive Scope)
- **Overview**: High-level summary of the forecast
- **Key Drivers**: Top 3-5 factors driving the forecast
- **Variances**: Significant changes from prior version/actuals
- **Risks & Flags**: Low confidence items, data quality issues
- **Recommendations**: Suggested actions for review

### Tone Options
- **formal**: Board-ready language, executive-appropriate
- **concise**: Bullet-point format, quick scan
- **analytical**: Detailed technical analysis with model insights

## Examples
- "Write executive commentary for the forecast" → scope=executive, tone=formal
- "Summarize the revenue category" → scope=category, category=Revenue
- "Give me a quick analysis" → scope=executive, tone=concise
- "Deep dive on the marketing line" → scope=line_item, line_item_name=marketing
