"""Primary/fallback provider orchestration with explicit degraded quality."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
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
        max_primary_age_seconds: int = 180,
    ):
        self.primary = primary
        self.fallback = fallback
        self.budget = budget
        self.primary_name = primary_name
        self.max_primary_age_seconds = max(1, int(max_primary_age_seconds))

    def _primary_is_fresh(self, price: MarketPrice, now: datetime) -> bool:
        ts = price.timestamp
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        else:
            ts = ts.astimezone(timezone.utc)
        age = (now - ts).total_seconds()
        return -30 <= age <= self.max_primary_age_seconds

    def get_prices(self, symbols: Sequence[str]) -> Sequence[MarketPrice]:
        unique = tuple(dict.fromkeys(s.strip().upper() for s in symbols if s.strip()))
        if not unique:
            return ()

        allowed = len(unique)
        if self.budget is not None:
            allowed = max(0, min(len(unique), self.budget.reserve(self.primary_name, len(unique))))

        primary_symbols = unique[:allowed]
        try:
            primary_prices = tuple(self.primary.get_prices(primary_symbols)) if primary_symbols else ()
        except Exception as exc:
            print(f"MARKET_DATA_BATCH_ERROR provider={self.primary_name} error={type(exc).__name__}:{exc}")
            primary_prices = ()

        now = datetime.now(timezone.utc)
        by_ticker: dict[str, MarketPrice] = {}
        for price in primary_prices:
            symbol = price.ticker.upper()
            if self._primary_is_fresh(price, now):
                by_ticker[symbol] = price
            else:
                print(
                    f"MARKET_DATA_STALE_PRIMARY provider={self.primary_name} "
                    f"symbol={symbol} timestamp={price.timestamp.isoformat()}"
                )

        # Missing includes credit-budget overflow, provider failures and stale
        # primary observations. All are retried on fallback instead of turning a
        # delayed primary feed into missed production alerts.
        missing = tuple(symbol for symbol in unique if symbol not in by_ticker)

        if missing and self.fallback is not None:
            try:
                fallback_prices = self.fallback.get_prices(missing)
            except Exception as exc:
                print(f"MARKET_DATA_BATCH_ERROR provider=FALLBACK error={type(exc).__name__}:{exc}")
                fallback_prices = ()
            for price in fallback_prices:
                by_ticker[price.ticker.upper()] = replace(price, data_quality="FALLBACK_OK")

        return tuple(by_ticker[s] for s in unique if s in by_ticker)
