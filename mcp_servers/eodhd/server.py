"""EODHD MCP server — stdio transport, self-describing per SPEC §5."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

sys.path.insert(0, str(Path(__file__).resolve().parent))

from client import (
    EODHDAPIError,
    EODHDAuthError,
    EODHDClient,
    EODHDError,
    EODHDNotFoundError,
    EODHDRateLimitError,
)

SERVER_NAME = "eodhd"
SERVER_VERSION = "0.1.0"

INSTRUCTIONS = """\
Standard market data tier (lab.md §2, priority 2): end-of-day prices, key fundamentals, \
earnings history/estimates, and ticker search across US, HK, China, and 70+ exchanges.

Use this server for: peer regression inputs (price history), Phase 2 fundamental snapshots, \
comps and valuation multiples, earnings quality/revision trends, and resolving company names \
to tickers.

Regional Analysts and Valuation Engine reach here first for market data when premium \
institutional data (tier 1) is unavailable. Sector Expert may use comps from fundamentals.

Limitations: EOD data only (not real-time); free/low tiers may restrict history depth; \
broker research and filing-grade financials are NOT provided here. HK/China coverage \
varies by symbol. Do not treat sell-side estimates embedded in fundamentals as ground truth.

When unavailable: escalate to premium institutional data if configured; otherwise use \
official filing repositories (tier 3) for financials and state data gaps explicitly in output.
"""

FALLBACK_STANDARD = (
    "Use official filing repositories (tier 3) for financials and state the data gap in output."
)
FALLBACK_PREMIUM = (
    "Retry later, or use premium institutional market data (tier 1) if configured."
)

_client: EODHDClient | None = None


def _get_client() -> EODHDClient:
    global _client
    if _client is None:
        load_dotenv()
        _client = EODHDClient()
    return _client


def _error(
    code: str,
    message: str,
    suggested_fallback: str,
) -> dict[str, Any]:
    return {
        "error": True,
        "code": code,
        "message": message,
        "suggested_fallback": suggested_fallback,
    }


def _map_exception(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, EODHDAuthError):
        return _error(
            "auth_error",
            str(exc),
            "Set EODHD_API_KEY in the environment. Until configured, use filing repositories (tier 3).",
        )
    if isinstance(exc, EODHDNotFoundError):
        return _error(
            "not_found",
            str(exc),
            "Verify ticker and exchange with search_ticker, or resolve via official filings.",
        )
    if isinstance(exc, EODHDRateLimitError):
        return _error(
            "rate_limit",
            str(exc),
            "Wait and retry, or use premium institutional data (tier 1) if available.",
        )
    if isinstance(exc, EODHDAPIError) and "timed out" in str(exc).lower():
        return _error("timeout", str(exc), FALLBACK_STANDARD)
    if isinstance(exc, EODHDAPIError):
        return _error("api_error", str(exc), FALLBACK_STANDARD)
    if isinstance(exc, EODHDError):
        return _error("api_error", str(exc), FALLBACK_STANDARD)
    return _error("api_error", str(exc), FALLBACK_STANDARD)


def _shape_fundamentals(raw: dict[str, Any], symbol: str) -> dict[str, Any]:
    highlights = raw.get("Highlights") or {}
    valuation = raw.get("Valuation") or {}
    general = raw.get("General") or {}

    return {
        "symbol": symbol,
        "name": general.get("Name"),
        "currency": general.get("CurrencyCode"),
        "sector": general.get("Sector"),
        "industry": general.get("Industry"),
        "market_cap": highlights.get("MarketCapitalization"),
        "revenue_ttm": highlights.get("RevenueTTM"),
        "earnings_share_ttm": highlights.get("EarningsShare"),
        "profit_margin": highlights.get("ProfitMargin"),
        "operating_margin_ttm": highlights.get("OperatingMarginTTM"),
        "pe_ratio": highlights.get("PERatio") or valuation.get("TrailingPE"),
        "forward_pe": valuation.get("ForwardPE"),
        "price_to_book": valuation.get("PriceBookMRQ"),
        "ev_to_ebitda": valuation.get("EnterpriseValueEbitda"),
        "dividend_yield": highlights.get("DividendYield"),
        "eps_estimate_current_year": highlights.get("EPSEstimateCurrentYear"),
        "eps_estimate_next_year": highlights.get("EPSEstimateNextYear"),
    }


def _shape_earnings(raw: dict[str, Any], symbol: str) -> dict[str, Any]:
    earnings = raw.get("Earnings") or {}
    history = earnings.get("History") or {}
    trend = earnings.get("Trend") or {}
    annual = earnings.get("Annual") or {}

    history_rows = []
    if isinstance(history, dict):
        for period, row in sorted(history.items(), reverse=True):
            if isinstance(row, dict):
                history_rows.append({"period": period, **row})

    return {
        "symbol": symbol,
        "history": history_rows,
        "trend": trend,
        "annual": annual,
    }


def _shape_price_history(
    rows: list[dict[str, Any]],
    symbol: str,
    from_date: str,
    to_date: str,
) -> dict[str, Any]:
    series = [
        {
            "date": row.get("date"),
            "open": row.get("open"),
            "high": row.get("high"),
            "low": row.get("low"),
            "close": row.get("close"),
            "adjusted_close": row.get("adjusted_close"),
            "volume": row.get("volume"),
        }
        for row in rows
    ]
    return {
        "symbol": symbol,
        "from_date": from_date,
        "to_date": to_date,
        "count": len(series),
        "series": series,
    }


def _shape_search_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "ticker": item.get("Code"),
            "exchange": item.get("Exchange"),
            "name": item.get("Name"),
            "type": item.get("Type"),
            "currency": item.get("Currency"),
            "country": item.get("Country"),
            "isin": item.get("ISIN"),
            "is_primary": item.get("isPrimary"),
        }
        for item in results
    ]


mcp = FastMCP(name=SERVER_NAME, instructions=INSTRUCTIONS)
mcp._mcp_server.version = SERVER_VERSION


@mcp.tool()
async def get_price_history(
    ticker: str,
    from_date: str,
    to_date: str,
    exchange: str = "US",
) -> dict[str, Any]:
    """Fetch daily OHLCV price history for a symbol.

    Use for peer regression (trailing returns), price-based comparisons, and chart context.
    Inputs: ticker (e.g. AAPL), ISO from_date/to_date (YYYY-MM-DD), exchange code (default US).
    Returns adjusted close and volume per day. Data tier: standard market data (lab.md §2).
    """
    try:
        client = _get_client()
        symbol = client.build_symbol(ticker, exchange)
        rows = await client.get_eod(ticker, exchange, from_date, to_date)
        if not rows:
            return _error(
                "not_found",
                f"No price history for {symbol} between {from_date} and {to_date}.",
                "Verify ticker/exchange with search_ticker or widen the date range.",
            )
        return _shape_price_history(rows, symbol, from_date, to_date)
    except Exception as exc:
        return _map_exception(exc)


@mcp.tool()
async def get_fundamentals(ticker: str, exchange: str = "US") -> dict[str, Any]:
    """Fetch key fundamental metrics for a symbol.

    Use for comps, Phase 2 fundamental snapshots, and valuation multiples.
    Returns market cap, revenue, margins, P/E, P/B, EV/EBITDA, dividend yield, and EPS estimates.
    Estimates are reference only — not filing-grade truth (lab.md §2 source hierarchy).
    """
    try:
        client = _get_client()
        symbol = client.build_symbol(ticker, exchange)
        raw = await client.get_fundamentals(ticker, exchange)
        return _shape_fundamentals(raw, symbol)
    except Exception as exc:
        return _map_exception(exc)


@mcp.tool()
async def get_earnings_history(ticker: str, exchange: str = "US") -> dict[str, Any]:
    """Fetch historical and forward earnings data for a symbol.

    Use for earnings quality, beat/miss history, and estimate revision trends.
    Returns quarterly history (EPS actual/estimate/surprise), forward trend estimates, and annual summaries.
    Forward estimates are sell-side/consensus reference — cite as estimates, not facts.
    """
    try:
        client = _get_client()
        symbol = client.build_symbol(ticker, exchange)
        raw = await client.get_fundamentals(ticker, exchange)
        shaped = _shape_earnings(raw, symbol)
        if not shaped["history"] and not shaped["trend"]:
            return _error(
                "not_found",
                f"No earnings history available for {symbol}.",
                "State earnings data gap explicitly; use filing repositories for reported EPS.",
            )
        return shaped
    except Exception as exc:
        return _map_exception(exc)


@mcp.tool()
async def search_ticker(query: str, exchange: str | None = None) -> dict[str, Any]:
    """Search for tickers by symbol, company name, or ISIN.

    Use when the task provides a company name and you need the correct ticker and exchange.
    Optional exchange filter (e.g. US, HK, SHG, SHE). Returns matching instruments with name and exchange.
    """
    try:
        client = _get_client()
        results = await client.search(query, exchange=exchange)
        matches = _shape_search_results(results)
        if not matches:
            return _error(
                "not_found",
                f"No tickers matched query '{query}'.",
                "Confirm spelling or resolve identifier via official filings or coverage.md.",
            )
        return {"query": query, "exchange_filter": exchange, "count": len(matches), "matches": matches}
    except Exception as exc:
        return _map_exception(exc)


def main() -> None:
    load_dotenv()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
