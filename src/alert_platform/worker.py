"""Shadow worker orchestration for Trading Alert Platform v1.2 FINAL.

This stage allows market-data reads in SHADOW mode while keeping automatic
trigger transitions, V3 execution and outbound notifications disabled.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol, Sequence
from uuid import UUID, uuid4

from .market_data import MarketDataProvider, MarketPrice, validate_market_price


@dataclass(frozen=True)
class ClaimedAlert:
    id: UUID
    ticker: str
    market: str
    valid_until: datetime
    next_check_at: datetime | None


@dataclass(frozen=True)
class WorkerConfig:
    claim_limit: int = 100
    enable_market_data: bool = False
    enable_auto_trigger: bool = False
    enable_v3: bool = False
    send_whatsapp: bool = False
    max_price_age_seconds: int = 120


class AlertRepository(Protocol):
    def claim_due_alerts(self, worker_id: UUID, limit: int) -> Sequence[ClaimedAlert]: ...


class MarketHours(Protocol):
    def any_relevant_market_open(self, now: datetime) -> bool: ...


@dataclass(frozen=True)
class WorkerResult:
    worker_id: UUID
    status: str
    claimed: int
    unique_tickers: int = 0
    valid_prices: int = 0
    invalid_prices: int = 0


def _guard_shadow_capabilities(config: WorkerConfig) -> None:
    if config.enable_auto_trigger or config.enable_v3 or config.send_whatsapp:
        raise RuntimeError(
            "Shadow worker cannot enable auto-trigger, V3 or WhatsApp"
        )


def run_shadow_cycle(
    repo: AlertRepository,
    market_hours: MarketHours,
    config: WorkerConfig,
    *,
    market_data: MarketDataProvider | None = None,
    now: datetime | None = None,
) -> WorkerResult:
    """Run a SHADOW worker cycle.

    Stage A: claim due alerts only.
    Stage B: optionally read one market price per unique ticker and validate it.

    This function intentionally performs no trigger transition, no V3 call and no
    outbound notification. Persist/release scheduling is wired in the next DB
    integration step so the pure orchestration remains independently testable.
    """

    _guard_shadow_capabilities(config)

    current_time = now or datetime.now(timezone.utc)
    worker_id = uuid4()

    if not market_hours.any_relevant_market_open(current_time):
        return WorkerResult(worker_id=worker_id, status="MARKET_CLOSED", claimed=0)

    claimed = tuple(repo.claim_due_alerts(worker_id, config.claim_limit))
    unique_tickers = tuple(dict.fromkeys(a.ticker for a in claimed))

    if not config.enable_market_data:
        return WorkerResult(
            worker_id=worker_id,
            status="SHADOW_OK",
            claimed=len(claimed),
            unique_tickers=len(unique_tickers),
        )

    if market_data is None:
        raise RuntimeError("market_data provider is required when enable_market_data=true")

    prices: Sequence[MarketPrice] = market_data.get_prices(unique_tickers)
    by_ticker = {price.ticker: price for price in prices}

    valid_prices = 0
    invalid_prices = 0
    for ticker in unique_tickers:
        valid, _code = validate_market_price(
            by_ticker.get(ticker),
            max_price_age_seconds=config.max_price_age_seconds,
            now=current_time,
        )
        if valid:
            valid_prices += 1
        else:
            invalid_prices += 1

    return WorkerResult(
        worker_id=worker_id,
        status="SHADOW_MARKET_DATA_OK",
        claimed=len(claimed),
        unique_tickers=len(unique_tickers),
        valid_prices=valid_prices,
        invalid_prices=invalid_prices,
    )
