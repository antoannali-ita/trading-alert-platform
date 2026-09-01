"""SESSION_REFRESH_V1 orchestration.

One claimed session processes a bounded batch, persists per-ticker state and
never emits trigger/V3/notification side effects.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Protocol, Sequence
from uuid import UUID

from .gap_detection import GapThresholds, classify_gap
from .market_data import MarketDataProvider, MarketPrice, validate_market_price
from .scheduling import PollingConfig, next_check_at


@dataclass(frozen=True)
class ClaimedSession:
    id: UUID
    market: str
    session_date: date
    opened_at: datetime
    refresh_due_at: datetime
    refresh_started_at: datetime | None
    status: str


@dataclass(frozen=True)
class RefreshItem:
    id: UUID
    ticker: str
    market: str
    priority_class: str
    status: str
    retry_count: int
    entry_min: Decimal | None = None
    entry_max: Decimal | None = None
    max_buy: Decimal | None = None


@dataclass(frozen=True)
class SessionRefreshConfig:
    batch_limit: int = 8
    max_duration_minutes: int = 15
    pending_open_retry_1_minutes: int = 2
    pending_open_retry_2_minutes: int = 5
    max_price_age_seconds: int = 120
    polling: PollingConfig = PollingConfig()
    gap_thresholds: GapThresholds = GapThresholds()


@dataclass(frozen=True)
class SessionRefreshResult:
    session_id: UUID
    status: str
    seeded: int
    claimed_items: int
    updated: int
    degraded: int
    pending_open: int
    no_open_data: int
    failed: int
    pending_remaining: int


class SessionRefreshRepository(Protocol):
    def seed_session_refresh_items(self, session_id: UUID, worker_id: UUID) -> int: ...
    def claim_session_refresh_items(self, session_id: UUID, worker_id: UUID, limit: int) -> Sequence[RefreshItem]: ...
    def update_session_refresh_item(self, session_id: UUID, worker_id: UUID, ticker: str, **fields) -> bool: ...
    def apply_session_price_to_alerts(self, session_id: UUID, worker_id: UUID, ticker: str, price: MarketPrice, next_check: datetime) -> int: ...
    def session_refresh_pending_count(self, session_id: UUID, worker_id: UUID) -> int: ...
    def complete_session_refresh(self, session_id: UUID, worker_id: UUID, result: dict) -> bool: ...
    def expire_session_refresh_if_needed(self, session_id: UUID, worker_id: UUID) -> bool: ...


def _next_check_for_refresh(now: datetime, item: RefreshItem, price: MarketPrice, polling: PollingConfig) -> datetime:
    anchors = [x for x in (item.entry_min, item.entry_max, item.max_buy) if x is not None and x > 0]
    if not anchors:
        return now + timedelta(minutes=polling.far_minutes)
    distance = min(abs(price.price - anchor) / price.price for anchor in anchors)
    return next_check_at(now, distance, polling)


def run_session_refresh_batch(
    repo: SessionRefreshRepository,
    *,
    session: ClaimedSession,
    worker_id: UUID,
    market_data: MarketDataProvider,
    config: SessionRefreshConfig = SessionRefreshConfig(),
    now: datetime | None = None,
) -> SessionRefreshResult:
    current = now or datetime.now(timezone.utc)
    seeded = repo.seed_session_refresh_items(session.id, worker_id)

    started = session.refresh_started_at or current
    if current >= started + timedelta(minutes=config.max_duration_minutes):
        repo.expire_session_refresh_if_needed(session.id, worker_id)
        pending = repo.session_refresh_pending_count(session.id, worker_id)
        return SessionRefreshResult(session.id, "COMPLETED_WITH_PENDING", seeded, 0, 0, 0, 0, 0, 0, max(0, pending))

    items = tuple(repo.claim_session_refresh_items(session.id, worker_id, config.batch_limit))
    symbols = tuple(item.ticker for item in items)
    try:
        prices = tuple(market_data.get_prices(symbols)) if symbols else ()
    except Exception:
        prices = ()
    by_ticker = {p.ticker.upper(): p for p in prices}

    updated = degraded = pending_open = no_open_data = failed = 0

    for item in items:
        price = by_ticker.get(item.ticker.upper())
        valid, code = validate_market_price(price, max_price_age_seconds=config.max_price_age_seconds, now=current)
        if not valid or price is None:
            repo.update_session_refresh_item(
                session.id, worker_id, item.ticker,
                status="FAILED", data_quality="ALL_PROVIDERS_FAILED", error_code=code,
            )
            failed += 1
            continue

        if price.open_price is None or price.previous_close is None:
            if item.retry_count <= 0:
                repo.update_session_refresh_item(
                    session.id, worker_id, item.ticker,
                    status="PENDING_OPEN", current_price=price.price,
                    price_timestamp=price.timestamp, provider=price.provider,
                    data_quality="PENDING_OPEN",
                    next_retry_at=current + timedelta(minutes=config.pending_open_retry_1_minutes),
                )
                pending_open += 1
                continue
            if item.retry_count == 1:
                repo.update_session_refresh_item(
                    session.id, worker_id, item.ticker,
                    status="PENDING_OPEN", current_price=price.price,
                    price_timestamp=price.timestamp, provider=price.provider,
                    data_quality="PENDING_OPEN",
                    next_retry_at=current + timedelta(minutes=config.pending_open_retry_2_minutes),
                )
                pending_open += 1
                continue
            repo.update_session_refresh_item(
                session.id, worker_id, item.ticker,
                status="NO_OPEN_DATA", current_price=price.price,
                price_timestamp=price.timestamp, provider=price.provider,
                data_quality="NO_OPEN_DATA",
            )
            no_open_data += 1
            continue

        gap = classify_gap(
            previous_close=price.previous_close,
            open_price=price.open_price,
            current_price=price.price,
            entry_min=item.entry_min,
            entry_max=item.entry_max,
            max_buy=item.max_buy,
            thresholds=config.gap_thresholds,
        )
        quality = "DEGRADED" if price.data_quality != "PRIMARY_OK" else "UPDATED"
        data_quality = "FALLBACK_OK" if quality == "DEGRADED" else "PRIMARY_OK"
        repo.update_session_refresh_item(
            session.id, worker_id, item.ticker,
            status=quality,
            current_price=price.price,
            price_timestamp=price.timestamp,
            open_price=price.open_price,
            previous_close=price.previous_close,
            high_price=price.high_price,
            low_price=price.low_price,
            volume=price.volume,
            provider=price.provider,
            data_quality=data_quality,
            gap_pct=gap.gap_pct,
            gap_flags=list(gap.flags),
            error_code=None,
            next_retry_at=None,
        )
        next_check = _next_check_for_refresh(current, item, price, config.polling)
        repo.apply_session_price_to_alerts(session.id, worker_id, item.ticker, price, next_check)
        if quality == "DEGRADED":
            degraded += 1
        else:
            updated += 1

    pending = repo.session_refresh_pending_count(session.id, worker_id)
    status = "IN_PROGRESS"
    if pending == 0:
        repo.complete_session_refresh(
            session.id,
            worker_id,
            {
                "updated": updated,
                "degraded": degraded,
                "pending_open": pending_open,
                "no_open_data": no_open_data,
                "failed": failed,
            },
        )
        status = "COMPLETED"

    return SessionRefreshResult(
        session.id, status, seeded, len(items), updated, degraded,
        pending_open, no_open_data, failed, max(0, pending),
    )
