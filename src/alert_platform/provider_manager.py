"""Primary/fallback provider orchestration with explicit degraded quality."""

from __future__ import annotations

from dataclasses import replace
from typing import Sequence

from .market_data import MarketDataProvider, MarketPrice


class ProviderManager:
    def __init__(self, primary: MarketDataProvider, fallback: MarketDataProvider | None = None):
        self.primary = primary
        self.fallback = fallback

    def get_prices(self, symbols: Sequence[str]) -> Sequence[MarketPrice]:
        unique = tuple(dict.fromkeys(s.strip().upper() for s in symbols if s.strip()))
        if not unique:
            return ()

        try:
            primary_prices = tuple(self.primary.get_prices(unique))
        except Exception:
            primary_prices = ()

        by_ticker = {p.ticker.upper(): p for p in primary_prices}
        missing = tuple(symbol for symbol in unique if symbol not in by_ticker)

        if missing and self.fallback is not None:
            try:
                fallback_prices = self.fallback.get_prices(missing)
            except Exception:
                fallback_prices = ()
            for price in fallback_prices:
                by_ticker[price.ticker.upper()] = replace(
                    price,
                    data_quality="FALLBACK_OK",
                )

        return tuple(by_ticker[s] for s in unique if s in by_ticker)
