# EODHD MCP Server

Standard market data capability for the research lab (lab.md §2, tier 2). Wraps the [EODHD REST API](https://eodhd.com/financial-apis/) as an MCP server with stdio transport. Provides end-of-day prices, key fundamentals, earnings history/estimates, and ticker search — no custom registry or metadata layer (SPEC §5).

**Quality tier:** Standard market data feeds (priority 2). Use when premium institutional data (tier 1) is unavailable. Not a substitute for official filings (tier 3) or broker research.

## Tools

| Tool | Description | Key inputs | Output |
|------|-------------|------------|--------|
| `get_price_history` | Daily OHLCV series | `ticker`, `from_date`, `to_date`, `exchange` (default `US`) | `{symbol, from_date, to_date, count, series[]}` |
| `get_fundamentals` | Key valuation and income metrics | `ticker`, `exchange` (default `US`) | `{symbol, market_cap, revenue_ttm, margins, pe_ratio, price_to_book, ev_to_ebitda, ...}` |
| `get_earnings_history` | Quarterly EPS history and forward trend | `ticker`, `exchange` (default `US`) | `{symbol, history[], trend, annual}` |
| `search_ticker` | Resolve name/symbol/ISIN to tickers | `query`, `exchange` (optional) | `{query, count, matches[]}` |

All tools return JSON-serializable dicts. On failure they return `{error: true, code, message, suggested_fallback}` — never `None`, never silent failure.

## Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `EODHD_API_KEY` | Yes | API token from https://eodhd.com/register |

Copy `.env.example` to `.env` in this directory or export the variable in the shell that launches the server.

## Run locally

```bash
cd mcp_servers/eodhd
pip install mcp httpx python-dotenv
export EODHD_API_KEY=your_key   # or use .env
python server.py
```

The server speaks MCP over stdio. Wire it from `lab.py` (future) as a subprocess MCP client.

## EODHD endpoints wrapped

| Tool | EODHD REST path |
|------|-----------------|
| `get_price_history` | `GET /api/eod/{TICKER}.{EXCHANGE}` |
| `get_fundamentals` | `GET /api/fundamentals/{TICKER}.{EXCHANGE}` |
| `get_earnings_history` | Same fundamentals endpoint (`Earnings` section) |
| `search_ticker` | `GET /api/search/{query}` |

Symbol format is `{TICKER}.{EXCHANGE}` (e.g. `AAPL.US`, `0700.HK`).

## Known limitations

- End-of-day prices only — not real-time or intraday.
- Free/low API plans limit historical depth (often ~1 year on free tier).
- Fundamentals estimates embedded in EODHD are consensus reference, not filing-grade truth.
- HK, China A-share, and smaller listings may have sparse or delayed coverage.
- `get_earnings_history` does not call the separate earnings calendar API; it parses the `Earnings` block from fundamentals.

## Fallback when this server is down

Per lab.md §2 and Director failure modes (prompts/director.md §8):

1. Retry once; if auth or rate limit, escalate after 2 failures.
2. Prefer premium institutional data (tier 1) if configured.
3. Degrade to official filing repositories (tier 3) for reported financials.
4. Stamp the actual data tier used in memo output; state gaps explicitly.

## Extend with new endpoints

1. Add a thin method on `client.py` — HTTP only, typed exceptions, no shaping.
2. Add a `@mcp.tool()` in `server.py` with an agent-readable docstring; shape response there.
3. Document the tool in this README table.
4. Follow the same error dict contract via `_map_exception`.

This layout (`client.py` + `server.py` + README + `.env.example`, stdio MCP, structured errors) is the template for future lab MCP servers.
