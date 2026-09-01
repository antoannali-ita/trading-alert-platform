"""Primary/fallback provider orchestration with explicit degraded quality."""

from __future__ import annotations

from dataclasses import replace
from typing import Protocol, Sequence

from .market_data import MarketDataProvider, MarketPrice


class CreditBudget(Protocol):
    def reserve(self, provider: str, requested: int) -> int: ...


class ProviderManager:
    def __init__(
        self,
        primary: MarketDataProvider,
        fallback: MarketDataProvider | None = None,
        *,
        budget: CreditBudget | None = None,
        primary_name: str = "TWELVE_DATA",
    ):
        self.primary = primary
        self.fallback = fallback
        self.budget = budget
        self.primary_name = primary_name

    def get_prices(self, symbols: Sequence[str]) -> Sequence[MarketPrice]:
        unique = tuple(dict.fromkeys(s.strip().upper() for s in symbols if s.strip()))
        if not unique:
            return ()

        allowed = len(unique)
        if self.budget is not None:
            allowed = max(0, min(len(unique), self.budget.reserve(self.primary_name, len(unique))))

        primary_symbols = unique[:allowed]
        overflow_symbols = unique[allowed:]
        try:
            primary_prices = tuple(self.primary.get_prices(primary_symbols)) if primary_symbols else ()
        except Exception:
            primary_prices = ()

        by_ticker = {p.ticker.upper(): p for p in primary_prices}
        missing = tuple(
            symbol for symbol in unique
            if symbol not in by_ticker
        )
        # overflow_symbols are intentionally part of missing and go straight to fallback.
        _ = overflow_symbols

        if missing and self.fallback is not None:
            try:
                fallback_prices = self.fallback.get_prices(missing)
            except Exception:
                fallback_prices = ()
            for price in fallback_prices:
                by_ticker[price.ticker.upper()] = replace(price, data_quality="FALLBACK_OK")

        return tuple(by_ticker[s] for s in unique if s in by_ticker)
