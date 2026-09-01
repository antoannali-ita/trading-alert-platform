"""Twelve Data market-data adapter.

The adapter is dependency-light and uses urllib so the public worker can stay thin.
No API key is stored in source control; it must be supplied at runtime.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Sequence
from urllib.parse import urlencode
from urllib.request import urlopen

from alert_platform.market_data import MarketPrice


class TwelveDataError(RuntimeError):
    pass


class TwelveDataProvider:
    base_url = "https://api.twelvedata.com/price"

    def __init__(self, api_key: str, *, timeout_seconds: float = 10.0):
        if not api_key:
            raise ValueError("Twelve Data API key is required")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    def get_prices(self, symbols: Sequence[str]) -> Sequence[MarketPrice]:
        unique = tuple(dict.fromkeys(s.strip().upper() for s in symbols if s.strip()))
        if not unique:
            return ()

        # MVP keeps one request per symbol behind the provider abstraction.
        # Batching can be enabled later without changing worker code.
        return tuple(self._get_one(symbol) for symbol in unique)

    def _get_one(self, symbol: str) -> MarketPrice:
        query = urlencode({"symbol": symbol, "apikey": self.api_key})
        url = f"{self.base_url}?{query}"
        try:
            with urlopen(url, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception as exc:  # network/provider errors are normalized here
            raise TwelveDataError(f"Twelve Data request failed for {symbol}") from exc

        if payload.get("status") == "error":
            raise TwelveDataError(payload.get("message") or f"Twelve Data error for {symbol}")

        raw_price = payload.get("price")
        if raw_price is None:
            raise TwelveDataError(f"Twelve Data returned no price for {symbol}")

        return MarketPrice(
            ticker=symbol,
            price=Decimal(str(raw_price)),
            timestamp=datetime.now(timezone.utc),
            market_status="UNKNOWN",
            provider="TWELVE_DATA",
        )
