"""financial_lookup skill — stock prices, economic indicators, and company financials."""

import logging
from typing import Any

from app.config import settings
from app.domain.base_skill import BaseSkill, SkillContext, SkillResult

logger = logging.getLogger(__name__)


class FinancialLookupSkill(BaseSkill):
    @property
    def name(self) -> str:
        return "financial_lookup"

    @property
    def description(self) -> str:
        return (
            "Look up financial data: stock prices, economic indicators (GDP, CPI, rates), "
            "market indices, and company financials. Uses yfinance and FRED API."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query_type": {
                    "type": "string",
                    "description": "Type of data: stock_price, economic_indicator, market_index, or company_financials",
                },
                "symbol": {
                    "type": "string",
                    "description": "Ticker symbol (e.g., AAPL, SPY) or FRED series ID (e.g., GDP, CPIAUCSL)",
                },
                "period": {
                    "type": "string",
                    "description": "Time period: 1d, 5d, 1mo, 3mo, 6mo, 1y, 5y, max",
                    "default": "3mo",
                },
                "metric": {
                    "type": "string",
                    "description": "For company_financials: income_statement, balance_sheet, cash_flow, info",
                    "default": "info",
                },
            },
            "required": ["query_type", "symbol"],
        }

    async def execute(self, params: dict[str, Any], context: SkillContext) -> SkillResult:
        query_type = params.get("query_type", "")
        symbol = params.get("symbol", "").strip().upper()

        if not query_type or not symbol:
            return SkillResult.fail("Both query_type and symbol are required.")

        handlers = {
            "stock_price": self._stock_price,
            "market_index": self._stock_price,
            "economic_indicator": self._economic_indicator,
            "company_financials": self._company_financials,
        }

        handler = handlers.get(query_type)
        if not handler:
            return SkillResult.fail(
                f"Unknown query_type: {query_type}. Use: stock_price, economic_indicator, "
                "market_index, or company_financials."
            )

        return await handler(params, symbol)

    async def _stock_price(self, params: dict, symbol: str) -> SkillResult:
        period = params.get("period", "3mo")
        try:
            import yfinance as yf

            ticker = yf.Ticker(symbol)
            info = ticker.info
            hist = ticker.history(period=period)

            if hist.empty:
                return SkillResult.fail(f"No data found for symbol: {symbol}")

            current = info.get("currentPrice") or info.get("regularMarketPrice") or float(hist["Close"].iloc[-1])
            prev_close = info.get("previousClose", float(hist["Close"].iloc[-2]) if len(hist) > 1 else current)
            change = current - prev_close
            change_pct = (change / prev_close * 100) if prev_close else 0

            name = info.get("shortName", symbol)
            market_cap = info.get("marketCap")
            pe_ratio = info.get("trailingPE")
            high_52w = info.get("fiftyTwoWeekHigh")
            low_52w = info.get("fiftyTwoWeekLow")

            lines = [
                f"**{name} ({symbol})**",
                f"Price: **${current:,.2f}** ({'+' if change >= 0 else ''}{change:.2f}, {'+' if change_pct >= 0 else ''}{change_pct:.2f}%)",
            ]
            if market_cap:
                mc = market_cap
                mc_str = f"${mc/1e12:.2f}T" if mc >= 1e12 else f"${mc/1e9:.2f}B" if mc >= 1e9 else f"${mc/1e6:.0f}M"
                lines.append(f"Market Cap: {mc_str}")
            if pe_ratio:
                lines.append(f"P/E Ratio: {pe_ratio:.1f}")
            if high_52w and low_52w:
                lines.append(f"52-Week Range: ${low_52w:,.2f} – ${high_52w:,.2f}")

            period_data = {
                "dates": hist.index.strftime("%Y-%m-%d").tolist()[-30:],
                "prices": [round(p, 2) for p in hist["Close"].tolist()[-30:]],
            }
            lines.append(f"\n*Showing {len(hist)} trading days ({period} period)*")

            return SkillResult.ok(
                message=f"{symbol}: ${current:,.2f} ({'+' if change_pct >= 0 else ''}{change_pct:.1f}%)",
                data={
                    "symbol": symbol,
                    "price": current,
                    "change": change,
                    "change_pct": change_pct,
                    "period_data": period_data,
                    "info": {
                        "name": name,
                        "market_cap": market_cap,
                        "pe_ratio": pe_ratio,
                    },
                },
                content_blocks=[self._text_block("\n".join(lines))],
            )

        except Exception as e:
            logger.error(f"yfinance error for {symbol}: {e}", exc_info=True)
            return SkillResult.fail(f"Failed to fetch stock data for {symbol}: {str(e)}")

    async def _economic_indicator(self, params: dict, symbol: str) -> SkillResult:
        if not settings.fred_api_key:
            return SkillResult.fail(
                "FRED API key is not configured. Set FRED_API_KEY in your .env file. "
                "Get a free key at https://fred.stlouisfed.org/docs/api/api_key.html"
            )

        common_indicators = {
            "GDP": "Gross Domestic Product",
            "CPIAUCSL": "Consumer Price Index (All Urban Consumers)",
            "UNRATE": "Unemployment Rate",
            "FEDFUNDS": "Federal Funds Rate",
            "DGS10": "10-Year Treasury Rate",
            "DGS2": "2-Year Treasury Rate",
            "UMCSENT": "Consumer Sentiment Index",
            "INDPRO": "Industrial Production Index",
            "HOUST": "Housing Starts",
            "PAYEMS": "Nonfarm Payrolls",
        }

        try:
            from fredapi import Fred

            fred = Fred(api_key=settings.fred_api_key)
            series = fred.get_series(symbol, observation_start="2023-01-01")

            if series.empty:
                return SkillResult.fail(f"No FRED data found for series: {symbol}")

            latest = float(series.iloc[-1])
            prev = float(series.iloc[-2]) if len(series) > 1 else latest
            change = latest - prev
            indicator_name = common_indicators.get(symbol, symbol)

            lines = [
                f"**{indicator_name}** (FRED: {symbol})",
                f"Latest Value: **{latest:,.2f}**",
                f"Change: {'+' if change >= 0 else ''}{change:.2f}",
                f"As of: {series.index[-1].strftime('%Y-%m-%d')}",
            ]

            recent = series.tail(12)
            lines.append(f"\nRecent trend ({len(recent)} observations):")
            for date, val in recent.items():
                lines.append(f"  {date.strftime('%Y-%m')}: {val:,.2f}")

            return SkillResult.ok(
                message=f"{indicator_name}: {latest:,.2f}",
                data={
                    "symbol": symbol,
                    "name": indicator_name,
                    "latest": latest,
                    "change": change,
                    "series": {
                        "dates": recent.index.strftime("%Y-%m-%d").tolist(),
                        "values": [round(float(v), 4) for v in recent.tolist()],
                    },
                },
                content_blocks=[self._text_block("\n".join(lines))],
            )

        except Exception as e:
            logger.error(f"FRED API error for {symbol}: {e}", exc_info=True)
            return SkillResult.fail(f"Failed to fetch FRED data for {symbol}: {str(e)}")

    async def _company_financials(self, params: dict, symbol: str) -> SkillResult:
        metric = params.get("metric", "info")
        try:
            import yfinance as yf

            ticker = yf.Ticker(symbol)
            name = ticker.info.get("shortName", symbol)

            if metric == "income_statement":
                df = ticker.income_stmt
                title = "Income Statement"
            elif metric == "balance_sheet":
                df = ticker.balance_sheet
                title = "Balance Sheet"
            elif metric == "cash_flow":
                df = ticker.cashflow
                title = "Cash Flow Statement"
            else:
                info = ticker.info
                key_metrics = {
                    "Revenue": info.get("totalRevenue"),
                    "Net Income": info.get("netIncomeToCommon"),
                    "EBITDA": info.get("ebitda"),
                    "Profit Margin": info.get("profitMargins"),
                    "Operating Margin": info.get("operatingMargins"),
                    "ROE": info.get("returnOnEquity"),
                    "Debt/Equity": info.get("debtToEquity"),
                    "Current Ratio": info.get("currentRatio"),
                    "Employees": info.get("fullTimeEmployees"),
                    "Sector": info.get("sector"),
                    "Industry": info.get("industry"),
                }
                lines = [f"**{name} ({symbol}) — Key Metrics**\n"]
                for k, v in key_metrics.items():
                    if v is not None:
                        if isinstance(v, float) and v < 1:
                            lines.append(f"- {k}: {v:.2%}")
                        elif isinstance(v, (int, float)) and v >= 1e6:
                            lines.append(f"- {k}: ${v/1e9:.2f}B" if v >= 1e9 else f"- {k}: ${v/1e6:.0f}M")
                        else:
                            lines.append(f"- {k}: {v}")

                return SkillResult.ok(
                    message=f"Key financial metrics for {name}",
                    data={"symbol": symbol, "metrics": key_metrics},
                    content_blocks=[self._text_block("\n".join(lines))],
                )

            if df is None or df.empty:
                return SkillResult.fail(f"No {title.lower()} data available for {symbol}")

            lines = [f"**{name} ({symbol}) — {title}**\n"]
            cols = df.columns[:4]
            for idx_name in df.index[:15]:
                row_vals = []
                for col in cols:
                    val = df.loc[idx_name, col]
                    if val is not None and not (hasattr(val, '__float__') and val != val):
                        val_f = float(val)
                        if abs(val_f) >= 1e9:
                            row_vals.append(f"${val_f/1e9:.1f}B")
                        elif abs(val_f) >= 1e6:
                            row_vals.append(f"${val_f/1e6:.0f}M")
                        else:
                            row_vals.append(f"{val_f:,.0f}")
                    else:
                        row_vals.append("—")
                lines.append(f"- **{idx_name}**: {' | '.join(row_vals)}")

            return SkillResult.ok(
                message=f"{title} for {name}",
                data={"symbol": symbol, "title": title},
                content_blocks=[self._text_block("\n".join(lines))],
            )

        except Exception as e:
            logger.error(f"yfinance financials error for {symbol}: {e}", exc_info=True)
            return SkillResult.fail(f"Failed to fetch financials for {symbol}: {str(e)}")
