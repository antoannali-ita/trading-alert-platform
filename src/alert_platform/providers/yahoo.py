"""Yahoo fallback adapter.

This adapter is fallback-only. Returned observations are always marked DEGRADED
and must never be silently treated as primary data.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Sequence
from urllib.parse import quote
from urllib.request import Request, urlopen

from alert_platform.market_data import MarketPrice


class YahooError(RuntimeError):
    pass


class YahooProvider:
    base_url = "https://query1.finance.yahoo.com/v8/finance/chart"

    def __init__(self, *, timeout_seconds: float = 10.0):
        self.timeout_seconds = timeout_seconds

    @staticmethod
    def _decimal(value):
        if value in (None, ""):
            return None
        return Decimal(str(value))

    def get_prices(self, symbols: Sequence[str]) -> Sequence[MarketPrice]:
        unique = tuple(dict.fromkeys(s.strip().upper() for s in symbols if s.strip()))
        prices: list[MarketPrice] = []
        for symbol in unique:
            try:
                prices.append(self._get_one(symbol))
            except Exception as exc:
                # One malformed/delisted symbol must never poison the whole fallback batch.
                print(f"MARKET_DATA_SYMBOL_ERROR provider=YAHOO symbol={symbol} error={type(exc).__name__}:{exc}")
        return tuple(prices)

    def _get_one(self, symbol: str) -> MarketPrice:
        url = f"{self.base_url}/{quote(symbol)}?interval=1m&range=1d"
        request = Request(url, headers={"User-Agent": "Mozilla/5.0 trading-alert-platform/0.1"})
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            raise YahooError(f"Yahoo request failed for {symbol}") from exc

        chart = payload.get("chart") or {}
        if chart.get("error"):
            raise YahooError(str(chart["error"]))
        results = chart.get("result") or []
        if not results:
            raise YahooError(f"Yahoo returned no chart result for {symbol}")

        result = results[0]
        meta = result.get("meta") or {}
        raw_price = meta.get("regularMarketPrice")
        raw_ts = meta.get("regularMarketTime")
        if raw_price is None or raw_ts is None:
            raise YahooError(f"Yahoo returned incomplete market data for {symbol}")

        indicators = ((result.get("indicators") or {}).get("quote") or [{}])[0]
        opens = [v for v in indicators.get("open", []) if v is not None]
        highs = [v for v in indicators.get("high", []) if v is not None]
        lows = [v for v in indicators.get("low", []) if v is not None]
        volumes = [v for v in indicators.get("volume", []) if v is not None]

        return MarketPrice(
            ticker=symbol,
            price=Decimal(str(raw_price)),
            timestamp=datetime.fromtimestamp(int(raw_ts), tz=timezone.utc),
            market_status=str(meta.get("marketState", "UNKNOWN")),
            provider="YAHOO",
            data_quality="FALLBACK_OK",
            open_price=self._decimal(opens[0] if opens else meta.get("regularMarketOpen")),
            previous_close=self._decimal(meta.get("previousClose") or meta.get("chartPreviousClose")),
            high_price=self._decimal(max(highs) if highs else meta.get("regularMarketDayHigh")),
            low_price=self._decimal(min(lows) if lows else meta.get("regularMarketDayLow")),
            volume=self._decimal(sum(volumes) if volumes else meta.get("regularMarketVolume")),
        )
