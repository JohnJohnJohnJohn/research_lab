"""Peer regression skill — Phase 1 Step 1 engine (SPEC §4, lab.md §4).

Cross-sectionally regresses trailing 12-month peer returns against candidate
fundamentals (P/E, P/B, revenue growth YoY, EBITDA margin, net debt/EBITDA,
ROE, dividend yield). Returns top 2–3 factors by |t-stat| for FactorRegime
assembly. Correlation discovery only — not causal proof.

When to call: Regional Analyst Phase 1, after peer set (20–30 names) is built.
Inject ``fetch_prices(ticker, from_date, to_date)`` and ``fetch_fundamentals(ticker)``
(e.g. wrapping EODHD MCP tools). Omit both to use direct EODHD REST
(``EODHD_API_KEY``). Tickers in ``TICKER.EXCHANGE`` form (e.g. ``9988.HK``).
"""

from __future__ import annotations

import os
import sys
import warnings
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import httpx
import numpy as np
import pandas as pd
import statsmodels.api as sm
from dotenv import load_dotenv

EODHD_BASE = "https://eodhd.com/api"
MIN_PEERS = 5
FACTORS: dict[str, str] = {
    "pe_ratio": "P/E ratio (trailing)",
    "price_to_book": "P/B ratio",
    "revenue_growth_yoy": "Revenue growth YoY",
    "ebitda_margin": "EBITDA margin",
    "net_debt_to_ebitda": "Net debt / EBITDA",
    "roe": "Return on equity (ROE)",
    "dividend_yield": "Dividend yield",
}


@dataclass
class PeerRegressionResult:
    primary_factors: list[dict[str, Any]]
    r_squared: float
    n_peers: int
    confidence: str
    data_gaps: list[str]
    regime_note: str
    as_of_date: str


def run_peer_regression(
    target_ticker: str,
    peer_tickers: list[str],
    fetch_prices: Callable[[str, str, str], dict[str, Any]] | None = None,
    fetch_fundamentals: Callable[[str], dict[str, Any]] | None = None,
    lookback_months: int = 12,
) -> PeerRegressionResult:
    """Run cross-sectional peer regression for Phase 1 factor discovery.

    ``target_ticker`` is context only; OLS runs on ``peer_tickers`` (target excluded).
    Returns ``PeerRegressionResult`` — never raises. Confidence: high if n>=20 and
    R²>=0.30; medium if n>=10 and R²>=0.15; low otherwise or if <5 valid price peers.
    """
    as_of = date.today().isoformat()
    gaps: list[str] = []
    try:
        get_prices = fetch_prices or _default_fetch_prices
        get_funds = fetch_fundamentals or _default_fetch_fundamentals
        to_date = as_of
        from_date = (date.today() - timedelta(days=lookback_months * 31)).isoformat()
        peers = [t for t in peer_tickers if t.upper() != target_ticker.upper()]
        rows: list[dict[str, Any]] = []

        for ticker in peers:
            px = get_prices(ticker, from_date, to_date)
            if px.get("error"):
                gaps.append(f"{ticker}: price fetch failed")
                continue
            ret = _trailing_return(px, lookback_months)
            if ret is None:
                gaps.append(f"{ticker}: insufficient price history")
                continue
            fund = get_funds(ticker)
            if fund.get("error"):
                gaps.append(f"{ticker}: fundamentals fetch failed")
                continue
            factors, fg = _extract_factors(fund)
            gaps.extend(fg)
            rows.append({"ticker": ticker, "return_12m": ret, **factors})

        if len(rows) < MIN_PEERS:
            return _make(as_of, [], 0.0, len(rows), "low", gaps,
                         f"Fewer than {MIN_PEERS} peers returned valid price data ({len(rows)} usable).")

        df = pd.DataFrame(rows)
        cols = [c for c in FACTORS if c in df.columns and df[c].notna().sum() >= MIN_PEERS]
        if not cols:
            return _make(as_of, [], 0.0, len(df), "low", gaps,
                         "All fundamental fetches failed or returned no usable fields.")

        reg = df[["return_12m", *cols]].dropna()
        if len(reg) < MIN_PEERS:
            return _make(as_of, [], 0.0, len(reg), "low", gaps,
                         "Insufficient peers with both returns and fundamentals.")

        model = sm.OLS(reg["return_12m"].astype(float), sm.add_constant(reg[cols].astype(float))).fit()
        primary = _top_factors(model, cols)
        r2 = float(model.rsquared) if model.rsquared == model.rsquared else 0.0
        n = int(model.nobs)
        note = (f"Peer regression on {n} comps: top factors by |t-stat| "
                "(correlation discovery, not causal proof).")
        if gaps:
            note += f" Data gaps: {len(gaps)}."
        return _make(as_of, primary, r2, n, _confidence(n, r2), gaps, note)
    except Exception as exc:
        warnings.warn(f"peer_regression failed: {exc}", file=sys.stderr)
        return _make(as_of, [], 0.0, 0, "low", gaps + [f"runtime error: {exc}"],
                     "Peer regression failed; returning low-confidence empty result.")


def _make(as_of: str, primary: list[dict], r2: float, n: int, conf: str,
          gaps: list[str], note: str) -> PeerRegressionResult:
    return PeerRegressionResult(primary, r2, n, conf, gaps, note, as_of)


def _split(symbol: str) -> tuple[str, str]:
    symbol = symbol.strip().upper()
    if "." in symbol:
        t, ex = symbol.rsplit(".", 1)
        return t, ex
    return symbol, "US"


def _num(v: Any) -> float | None:
    try:
        out = float(v)
        return None if np.isnan(out) or np.isinf(out) else out
    except (TypeError, ValueError):
        return None


def _trailing_return(data: dict[str, Any], months: int) -> float | None:
    series = sorted(
        ((str(r.get("date")), _num(r.get("adjusted_close") or r.get("close")))
         for r in (data.get("series") or [])),
        key=lambda x: x[0],
    )
    series = [(d, p) for d, p in series if p is not None and d]
    if len(series) < 2:
        return None
    end = series[-1][1]
    cutoff = (date.today() - timedelta(days=months * 30)).isoformat()
    start = next((p for d, p in reversed(series) if d <= cutoff), series[0][1])
    return None if start == 0 else (end / start) - 1.0


def _latest_yearly(section: dict[str, Any] | None) -> dict[str, Any] | None:
    yearly = (section or {}).get("yearly")
    if not isinstance(yearly, dict) or not yearly:
        return None
    row = sorted(yearly.items(), reverse=True)[0][1]
    return row if isinstance(row, dict) else None


def _extract_factors(data: dict[str, Any]) -> tuple[dict[str, float | None], list[str]]:
    gaps: list[str] = []
    if data.get("error"):
        return {}, [str(data.get("message", "fundamentals error"))]

    if "pe_ratio" in data or "price_to_book" in data:
        return {
            "pe_ratio": _num(data.get("pe_ratio")),
            "price_to_book": _num(data.get("price_to_book")),
            "revenue_growth_yoy": None,
            "ebitda_margin": _num(data.get("operating_margin_ttm")),
            "net_debt_to_ebitda": None,
            "roe": None,
            "dividend_yield": _num(data.get("dividend_yield")),
        }, gaps

    h = data.get("Highlights") or {}
    v = data.get("Valuation") or {}
    fin = data.get("Financials") or {}
    inc, bal = fin.get("Income_Statement") or {}, fin.get("Balance_Sheet") or {}

    rev_g = _num(h.get("QuarterlyRevenueGrowthYOY"))
    if rev_g is None:
        yearly = _latest_yearly(inc)
        prev = None
        ys = sorted((inc.get("yearly") or {}).items(), reverse=True) if isinstance(inc, dict) else []
        if len(ys) >= 2 and isinstance(ys[1][1], dict):
            prev = _num(ys[1][1].get("totalRevenue"))
        if yearly and prev:
            cur = _num(yearly.get("totalRevenue"))
            rev_g = (cur / prev - 1.0) if cur and prev else None
        if rev_g is None:
            gaps.append("revenue growth YoY unavailable")

    margin = _num(h.get("OperatingMarginTTM"))
    if margin is None:
        y = _latest_yearly(inc)
        rev, ebitda = _num((y or {}).get("totalRevenue")), _num((y or {}).get("ebitda"))
        margin = (ebitda / rev) if rev and ebitda and rev else None

    nd_e = _net_debt_ebitda(inc, bal)
    if nd_e is None:
        gaps.append("net debt / EBITDA unavailable")

    return {
        "pe_ratio": _num(h.get("PERatio") or v.get("TrailingPE")),
        "price_to_book": _num(v.get("PriceBookMRQ")),
        "revenue_growth_yoy": rev_g,
        "ebitda_margin": margin,
        "net_debt_to_ebitda": nd_e,
        "roe": _num(h.get("ReturnOnEquityTTM")),
        "dividend_yield": _num(h.get("DividendYield")),
    }, gaps


def _net_debt_ebitda(inc: dict[str, Any], bal: dict[str, Any]) -> float | None:
    bs, row = _latest_yearly(bal), _latest_yearly(inc)
    if not bs or not row:
        return None
    debt = (_num(bs.get("shortLongTermDebtTotal")) or 0) + (_num(bs.get("longTermDebt")) or 0)
    cash = _num(bs.get("cash")) or _num(bs.get("cashAndShortTermInvestments")) or 0
    ebitda = _num(row.get("ebitda"))
    return None if not ebitda else (debt - cash) / ebitda


def _top_factors(model: Any, cols: list[str]) -> list[dict[str, Any]]:
    ranked = sorted(
        (
            {
                "factor": FACTORS[c],
                "direction": "+" if float(model.params[c]) >= 0 else "-",
                "t_stat": round(float(model.tvalues[c]), 3),
                "note": "correlation discovery, not causal proof",
            }
            for c in cols if c in model.params
        ),
        key=lambda r: abs(r["t_stat"]),
        reverse=True,
    )
    return ranked[:3]


def _confidence(n: int, r2: float) -> str:
    if n >= 20 and r2 >= 0.30:
        return "high"
    if n >= 10 and r2 >= 0.15:
        return "medium"
    return "low"


def _default_fetch_prices(ticker: str, from_date: str, to_date: str) -> dict[str, Any]:
    code, ex = _split(ticker)
    return _eodhd_get(
        f"eod/{code}.{ex}",
        {"from": from_date, "to": to_date, "period": "d", "order": "a"},
        lambda rows: {
            "symbol": f"{code}.{ex}",
            "series": [{"date": r.get("date"), "adjusted_close": r.get("adjusted_close"),
                        "close": r.get("close")} for r in rows],
        },
    )


def _default_fetch_fundamentals(ticker: str) -> dict[str, Any]:
    code, ex = _split(ticker)
    return _eodhd_get(f"fundamentals/{code}.{ex}", {}, lambda d: d)


def _eodhd_get(path: str, params: dict[str, Any], shape: Callable[[Any], Any]) -> dict[str, Any]:
    try:
        load_dotenv()
        key = os.environ.get("EODHD_API_KEY", "").strip()
        if not key:
            return {"error": True, "message": "EODHD_API_KEY not set"}
        with httpx.Client(timeout=30.0) as client:
            resp = client.get(EODHD_BASE + "/" + path.lstrip("/"),
                              params={"api_token": key, "fmt": "json", **params})
            resp.raise_for_status()
            data = resp.json()
        if not data:
            return {"error": True, "message": f"No data from {path}"}
        return shape(data)
    except Exception as exc:
        return {"error": True, "message": str(exc)}
