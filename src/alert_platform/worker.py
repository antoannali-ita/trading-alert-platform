"""Shadow worker orchestration for Trading Alert Platform v1.2 FINAL."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Protocol, Sequence
from uuid import UUID, uuid4

from .market_data import MarketDataProvider, MarketPrice, validate_market_price
from .scheduling import PollingConfig, distance_to_trigger, next_check_at


@dataclass(frozen=True)
class ClaimedAlert:
    id: UUID
    ticker: str
    market: str
    alert_type: str
    threshold: Decimal | None
    threshold_min: Decimal | None
    threshold_max: Decimal | None
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
    polling: PollingConfig = PollingConfig()


class AlertRepository(Protocol):
    def claim_due_alerts(self, worker_id: UUID, limit: int) -> Sequence[ClaimedAlert]: ...
    def release_alert(self, alert_id: UUID, worker_id: UUID, next_check_at: datetime) -> bool: ...
    def record_alert_run(
        self,
        *,
        worker_id: UUID,
        alert_id: UUID,
        ticker: str,
        price: Decimal | None,
        price_timestamp: datetime | None,
        provider: str | None,
        trigger_hit: bool | None,
        error_code: str | None,
        duration_ms: int | None = None,
    ) -> int: ...


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
    released: int = 0


def _guard_shadow_capabilities(config: WorkerConfig) -> None:
    if config.enable_auto_trigger or config.enable_v3 or config.send_whatsapp:
        raise RuntimeError("Shadow worker cannot enable auto-trigger, V3 or WhatsApp")


def _record_run_if_supported(repo, **kwargs) -> int:
    recorder = getattr(repo, "record_alert_run", None)
    if recorder is None:
        return 0
    return int(recorder(**kwargs))


def run_shadow_cycle(
    repo: AlertRepository,
    market_hours: MarketHours,
    config: WorkerConfig,
    *,
    market_data: MarketDataProvider | None = None,
    now: datetime | None = None,
) -> WorkerResult:
    """Run one SHADOW worker cycle without trigger/V3/WhatsApp side effects."""

    _guard_shadow_capabilities(config)
    current_time = now or datetime.now(timezone.utc)
    worker_id = uuid4()

    if not market_hours.any_relevant_market_open(current_time):
        return WorkerResult(worker_id=worker_id, status="MARKET_CLOSED", claimed=0)

    claimed = tuple(repo.claim_due_alerts(worker_id, config.claim_limit))
    unique_tickers = tuple(dict.fromkeys(a.ticker.upper() for a in claimed))

    if not config.enable_market_data:
        return WorkerResult(worker_id=worker_id, status="SHADOW_OK", claimed=len(claimed), unique_tickers=len(unique_tickers))

    if market_data is None:
        raise RuntimeError("market_data provider is required when enable_market_data=true")

    try:
        prices: Sequence[MarketPrice] = market_data.get_prices(unique_tickers)
    except Exception:
        prices = ()
    by_ticker = {price.ticker.upper(): price for price in prices}

    valid_prices = 0
    invalid_prices = 0
    released = 0

    for alert in claimed:
        price = by_ticker.get(alert.ticker.upper())
        valid, code = validate_market_price(price, max_price_age_seconds=config.max_price_age_seconds, now=current_time)

        if valid and price is not None:
            valid_prices += 1
            distance = distance_to_trigger(
                alert_type=alert.alert_type,
                price=price.price,
                threshold=alert.threshold,
                threshold_min=alert.threshold_min,
                threshold_max=alert.threshold_max,
            )
            release_at = next_check_at(current_time, distance, config.polling)
            _record_run_if_supported(
                repo,
                worker_id=worker_id,
                alert_id=alert.id,
                ticker=alert.ticker,
                price=price.price,
                price_timestamp=price.timestamp,
                provider=price.provider,
                trigger_hit=None,
                error_code=None,
                duration_ms=None,
            )
        else:
            invalid_prices += 1
            retry_count = _record_run_if_supported(
                repo,
                worker_id=worker_id,
                alert_id=alert.id,
                ticker=alert.ticker,
                price=price.price if price else None,
                price_timestamp=price.timestamp if price else None,
                provider=price.provider if price else None,
                trigger_hit=None,
                error_code=code,
                duration_ms=None,
            )
            release_at = current_time + timedelta(minutes=1 if retry_count <= 0 else 5)

        if repo.release_alert(alert.id, worker_id, release_at):
            released += 1

    return WorkerResult(
        worker_id=worker_id,
        status="SHADOW_MARKET_DATA_OK",
        claimed=len(claimed),
        unique_tickers=len(unique_tickers),
        valid_prices=valid_prices,
        invalid_prices=invalid_prices,
        released=released,
    )
