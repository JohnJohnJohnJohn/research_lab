"""Thin async HTTP client for the EODHD REST API."""

from __future__ import annotations

import os
from typing import Any

import httpx
from dotenv import load_dotenv

BASE_URL = "https://eodhd.com/api"
DEFAULT_TIMEOUT = 30.0


class EODHDError(Exception):
    """Base exception for EODHD client failures."""


class EODHDAuthError(EODHDError):
    """Missing or invalid API key."""


class EODHDNotFoundError(EODHDError):
    """Ticker or resource not found."""


class EODHDRateLimitError(EODHDError):
    """API rate limit exceeded."""


class EODHDAPIError(EODHDError):
    """General API or transport failure."""


class EODHDClient:
    """Minimal EODHD HTTP wrapper — no business logic."""

    def __init__(
        self,
        api_key: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        load_dotenv()
        self.api_key = api_key or os.environ.get("EODHD_API_KEY")
        self.timeout = timeout

    def _require_api_key(self) -> str:
        if not self.api_key:
            raise EODHDAuthError("EODHD_API_KEY is not set")
        return self.api_key

    @staticmethod
    def build_symbol(ticker: str, exchange: str) -> str:
        ticker = ticker.strip()
        if "." in ticker:
            return ticker
        return f"{ticker}.{exchange}"

    async def _get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        api_key = self._require_api_key()
        query = {"api_token": api_key, "fmt": "json", **(params or {})}
        url = f"{BASE_URL}/{path.lstrip('/')}"

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(url, params=query)
        except httpx.TimeoutException as exc:
            raise EODHDAPIError(f"Request timed out: {url}") from exc
        except httpx.HTTPError as exc:
            raise EODHDAPIError(f"HTTP request failed: {exc}") from exc

        if response.status_code in (401, 403):
            body = response.text.strip()
            if "api token" in body.lower() or not self.api_key:
                raise EODHDAuthError(body or "Authentication failed")
            raise EODHDNotFoundError(body or f"Not found or forbidden: {path}")

        if response.status_code == 404:
            raise EODHDNotFoundError(response.text.strip() or f"Not found: {path}")

        if response.status_code == 429:
            raise EODHDRateLimitError(response.text.strip() or "Rate limit exceeded")

        if response.status_code >= 400:
            raise EODHDAPIError(
                f"EODHD API error {response.status_code}: {response.text.strip()}"
            )

        try:
            return response.json()
        except ValueError as exc:
            text = response.text.strip()
            if text.lower() == "not found":
                raise EODHDNotFoundError(text) from exc
            raise EODHDAPIError(f"Non-JSON response from EODHD: {text[:200]}") from exc

    async def get_eod(
        self,
        ticker: str,
        exchange: str,
        from_date: str,
        to_date: str,
    ) -> list[dict[str, Any]]:
        symbol = self.build_symbol(ticker, exchange)
        data = await self._get(
            f"eod/{symbol}",
            {"from": from_date, "to": to_date, "period": "d", "order": "a"},
        )
        if not isinstance(data, list):
            raise EODHDAPIError(f"Unexpected EOD response type for {symbol}")
        return data

    async def get_fundamentals(self, ticker: str, exchange: str) -> dict[str, Any]:
        symbol = self.build_symbol(ticker, exchange)
        data = await self._get(f"fundamentals/{symbol}")
        if not isinstance(data, dict):
            raise EODHDAPIError(f"Unexpected fundamentals response type for {symbol}")
        return data

    async def search(
        self,
        query: str,
        exchange: str | None = None,
        limit: int = 15,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": limit}
        if exchange:
            params["exchange"] = exchange
        data = await self._get(f"search/{query}", params)
        if not isinstance(data, list):
            raise EODHDAPIError("Unexpected search response type")
        return data
