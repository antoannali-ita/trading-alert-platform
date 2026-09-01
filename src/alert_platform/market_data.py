"""Provider-agnostic market data contract and data-quality checks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Protocol, Sequence


@dataclass(frozen=True)
class MarketPrice:
    ticker: str
    price: Decimal
    timestamp: datetime
    market_status: str
    provider: str


class MarketDataProvider(Protocol):
    def get_prices(self, symbols: Sequence[str]) -> Sequence[MarketPrice]: ...


def price_age_seconds(price: MarketPrice, *, now: datetime | None = None) -> float:
    current = now or datetime.now(timezone.utc)
    ts = price.timestamp
    if ts.tzinfo is None:
        raise ValueError("market price timestamp must be timezone-aware")
    return max(0.0, (current - ts).total_seconds())


def validate_market_price(
    price: MarketPrice | None,
    *,
    max_price_age_seconds: int,
    now: datetime | None = None,
) -> tuple[bool, str]:
    if price is None:
        return False, "PROVIDER_ERROR"
    if price.price <= 0:
        return False, "PROVIDER_ERROR"
    if not price.provider:
        return False, "PROVIDER_ERROR"
    if price.timestamp.tzinfo is None:
        return False, "DATA_STALE"
    if price_age_seconds(price, now=now) > max_price_age_seconds:
        return False, "DATA_STALE"
    return True, "CHECK_OK"
