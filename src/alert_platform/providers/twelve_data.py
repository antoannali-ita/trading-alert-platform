"""Twelve Data market-data adapter."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Sequence
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from alert_platform.market_data import MarketPrice


class TwelveDataError(RuntimeError):
    pass


class TwelveDataProvider:
    base_url = "https://api.twelvedata.com/quote"

    def __init__(self, api_key: str, *, timeout_seconds: float = 10.0):
        if not api_key:
            raise ValueError("Twelve Data API key is required")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    @staticmethod
    def _decimal(value):
        if value in (None, ""):
            return None
        return Decimal(str(value))

    def get_prices(self, symbols: Sequence[str]) -> Sequence[MarketPrice]:
        unique = tuple(dict.fromkeys(s.strip().upper() for s in symbols if s.strip()))
        if not unique:
            return ()
        return tuple(self._get_one(symbol) for symbol in unique)

    def _get_one(self, symbol: str) -> MarketPrice:
        query = urlencode({"symbol": symbol, "apikey": self.api_key})
        url = f"{self.base_url}?{query}"
        request = Request(url, headers={"User-Agent": "trading-alert-platform-worker/0.1"})
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            raise TwelveDataError(f"Twelve Data request failed for {symbol}") from exc

        if payload.get("status") == "error" or payload.get("code"):
            message = payload.get("message") or f"Twelve Data error for {symbol}"
            raise TwelveDataError(message)

        raw_price = payload.get("close")
        raw_timestamp = payload.get("timestamp")
        if raw_price is None:
            raise TwelveDataError(f"Twelve Data returned no close price for {symbol}")
        if raw_timestamp is None:
            raise TwelveDataError(f"Twelve Data returned no timestamp for {symbol}")

        try:
            provider_timestamp = datetime.fromtimestamp(int(raw_timestamp), tz=timezone.utc)
        except (TypeError, ValueError, OSError) as exc:
            raise TwelveDataError(
                f"Twelve Data returned invalid timestamp for {symbol}: {raw_timestamp!r}"
            ) from exc

        return MarketPrice(
            ticker=symbol,
            price=Decimal(str(raw_price)),
            timestamp=provider_timestamp,
            market_status=str(payload.get("is_market_open", "UNKNOWN")),
            provider="TWELVE_DATA",
            data_quality="PRIMARY_OK",
            open_price=self._decimal(payload.get("open")),
            previous_close=self._decimal(payload.get("previous_close")),
            high_price=self._decimal(payload.get("high")),
            low_price=self._decimal(payload.get("low")),
            volume=self._decimal(payload.get("volume")),
        )
