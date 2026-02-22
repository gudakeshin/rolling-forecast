---
name: web_search
description: >
  Search the web for current information using Perplexity Sonar API.
  Returns grounded, synthesized answers with inline citations — not raw links.
  Ideal for market news, competitor analysis, regulatory updates, economic trends,
  and any real-time data the user needs for forecasting context.
required_role: null
version: "1.0"
tags: [search, web, perplexity, research, news]
parameters:
  - name: query
    type: string
    description: The search query for web search
    required: true
  - name: search_focus
    type: string
    description: "Focus area: internet (general), finance, or news"
    default: internet
  - name: detail_level
    type: string
    description: "Level of detail: quick (sonar) or detailed (sonar-pro)"
    default: detailed
---

# Web Search

## When to Use
Use this skill when the user asks about:
- Current market conditions, stock prices, or economic indicators
- Recent news about companies, industries, or regulations
- Competitor analysis or market trends
- Any information that requires up-to-date web data
- "What's the latest on...?" or "What are current trends in...?"
- Macro-economic factors affecting forecasts (CPI, GDP, interest rates)

## Behavior
- Uses Perplexity Sonar API for grounded web search with citations
- `sonar` model for quick lookups, `sonar-pro` for complex financial queries
- Returns synthesized answers (not just links) with source attribution
- Focus areas: `internet` (general), `finance` (markets/economic), `news` (current events)

## Examples
- "What are the latest CPI trends?" → query="latest CPI trends US 2026", search_focus="finance"
- "What's happening with semiconductor supply chains?" → query="semiconductor supply chain outlook 2026"
- "Any recent news about Tesla earnings?" → query="Tesla earnings recent", search_focus="news"
- "What are current interest rate expectations?" → query="Federal Reserve interest rate outlook", search_focus="finance"
