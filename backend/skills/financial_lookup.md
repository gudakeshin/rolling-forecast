---
name: financial_lookup
description: >
  Look up financial data including stock prices, market indices, economic
  indicators (GDP, CPI, unemployment, interest rates), and company financials
  (income statements, balance sheets, cash flow). Uses yfinance (free, no key)
  and FRED API (free key required for economic data).
required_role: null
version: "1.0"
tags: [finance, stocks, economics, data, market]
parameters:
  - name: query_type
    type: string
    description: "Type of data: stock_price, economic_indicator, market_index, company_financials"
    required: true
    enum: [stock_price, economic_indicator, market_index, company_financials]
  - name: symbol
    type: string
    description: "Ticker symbol (AAPL, SPY) or FRED series ID (GDP, CPIAUCSL, UNRATE, FEDFUNDS)"
    required: true
  - name: period
    type: string
    description: "Time period: 1d, 5d, 1mo, 3mo, 6mo, 1y, 5y, max"
    default: "3mo"
  - name: metric
    type: string
    description: "For company_financials: income_statement, balance_sheet, cash_flow, info"
    default: info
---

# Financial Lookup

## When to Use
Use this skill when the user asks about:
- Stock prices or market performance ("What's Apple trading at?")
- Economic indicators ("What's the current CPI?" "What's GDP growth?")
- Market indices ("How is the S&P 500 doing?")
- Company financial statements ("Show me Tesla's income statement")
- Macro-economic data relevant to forecast assumptions

## Data Sources
- **Stock/Market**: yfinance — free, no API key needed
- **Economic Indicators**: FRED API — free key from fred.stlouisfed.org
  - Common series: GDP, CPIAUCSL (CPI), UNRATE (unemployment), FEDFUNDS, DGS10 (10yr Treasury)

## Examples
- "What's Apple stock price?" → query_type=stock_price, symbol=AAPL
- "How is the S&P 500?" → query_type=market_index, symbol=SPY
- "What's the latest CPI?" → query_type=economic_indicator, symbol=CPIAUCSL
- "Show Tesla's income statement" → query_type=company_financials, symbol=TSLA, metric=income_statement
- "What's the unemployment rate?" → query_type=economic_indicator, symbol=UNRATE
- "Get Microsoft's key financial metrics" → query_type=company_financials, symbol=MSFT, metric=info
