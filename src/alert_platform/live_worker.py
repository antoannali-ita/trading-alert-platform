"""Production alert evaluation and multichannel delivery."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Callable
from uuid import uuid4

from .market_data import MarketDataProvider, validate_market_price
from .scheduling import PollingConfig, distance_to_trigger, next_check_at
from .worker import AlertRepository, MarketHours


@dataclass(frozen=True)
class LiveResult:
    status: str
    claimed: int = 0
    checked: int = 0
    triggered: int = 0
    sent: int = 0
    errors: int = 0
    released: int = 0
    last_error: str | None = None
    error_details: tuple[str, ...] = ()


def is_triggered(alert_type: str, price: Decimal, threshold: Decimal | None,
                 threshold_min: Decimal | None, threshold_max: Decimal | None) -> bool:
    kind = alert_type.upper()
    if kind in {"PRICE_BELOW", "MAX_BUY", "PULLBACK", "SUPPORT"}:
        return threshold is not None and price <= threshold
    if kind in {"PRICE_ABOVE", "BREAKOUT", "RESISTANCE"}:
        return threshold is not None and price >= threshold
    if kind == "ENTRY_ZONE":
        return threshold_min is not None and threshold_max is not None and threshold_min <= price <= threshold_max
    return False


def _message(alert, price: Decimal) -> str:
    if alert.alert_type == "ENTRY_ZONE":
        condition = f"zona {alert.threshold_min}-{alert.threshold_max}"
    else:
        condition = f"{alert.alert_type} {alert.threshold}"
    return (
        f"🚨 ALERT PLATFORM\n{alert.ticker} ({alert.market})\n"
        f"Prezzo: {price}\nCondizione: {condition}\n"
        f"ID: {alert.id}"
    )


def _canonical_market(value: str) -> str:
    market = str(value or "").upper().strip()
    if market == "ITALY":
        return "ITALIA"
    return market


def _provider_symbol(ticker: str, market: str) -> str:
    symbol = str(ticker or "").upper().strip()
    if _canonical_market(market) == "ITALIA" and symbol and not symbol.endswith(".MI"):
        return f"{symbol}.MI"
    return symbol


def run_live_cycle(repo: AlertRepository, market_hours: MarketHours, market_data: MarketDataProvider,
                   send_message: Callable[[str], str], *, now: datetime | None = None,
                   claim_limit: int = 100, max_price_age_seconds: int = 180,
                   polling: PollingConfig = PollingConfig()) -> LiveResult:
    current = now or datetime.now(timezone.utc)
    if not market_hours.any_relevant_market_open(current):
        return LiveResult(status="MARKET_CLOSED")
    worker_id = uuid4()

    # Do not claim alerts belonging to a closed exchange. Otherwise, while Milan
    # is open and the US is closed, stale US quotes are incorrectly counted as
    # production errors. Use the repository's atomic exclusion RPC when both the
    # calendar and repository support it; keep the generic fallback for tests and
    # alternate repository implementations.
    market_open = getattr(market_hours, "is_market_open", None)
    exclusion_claim = getattr(repo, "claim_due_alerts_excluding_markets", None)
    closed_markets: tuple[str, ...] = ()
    if callable(market_open) and callable(exclusion_claim):
        closed_markets = tuple(
            market for market in ("USA", "ITALIA")
            if not market_open(market, current)
        )
    if closed_markets and callable(exclusion_claim):
        claimed = tuple(exclusion_claim(worker_id, claim_limit, closed_markets))
    else:
        claimed = tuple(repo.claim_due_alerts(worker_id, claim_limit))

    requested_by_alert = {
        alert.id: _provider_symbol(alert.ticker, alert.market)
        for alert in claimed
    }
    symbols = tuple(dict.fromkeys(requested_by_alert.values()))
    provider_error: str | None = None
    try:
        prices = market_data.get_prices(symbols)
    except Exception as exc:
        provider_error = f"MARKET_DATA_BATCH_{type(exc).__name__}: {exc}"
        print(provider_error)
        prices = ()
    by_ticker = {p.ticker.upper(): p for p in prices}

    checked = triggered = sent = errors = released = 0
    last_error: str | None = provider_error
    error_details: list[str] = []

    for alert in claimed:
        requested_symbol = requested_by_alert[alert.id]
        price = by_ticker.get(requested_symbol.upper())
        valid, code = validate_market_price(price, max_price_age_seconds=max_price_age_seconds, now=current)
        if not valid or price is None:
            errors += 1
            detail = (
                f"PRICE_ERROR ticker={alert.ticker} market={alert.market} "
                f"symbol={requested_symbol} code={code or 'NO_PRICE'}"
            )
            error_details.append(detail)
            last_error = detail
            print(detail)
            repo.record_alert_run(worker_id=worker_id, alert_id=alert.id, ticker=alert.ticker,
                price=None if price is None else price.price,
                price_timestamp=None if price is None else price.timestamp,
                provider=None if price is None else price.provider, trigger_hit=False, error_code=code or "NO_PRICE")
            if repo.release_alert(alert.id, worker_id, current + timedelta(minutes=5)):
                released += 1
            continue

        checked += 1
        hit = is_triggered(alert.alert_type, price.price, alert.threshold, alert.threshold_min, alert.threshold_max)
        repo.record_alert_run(worker_id=worker_id, alert_id=alert.id, ticker=alert.ticker,
            price=price.price, price_timestamp=price.timestamp, provider=price.provider,
            trigger_hit=hit, error_code=None)
        if hit:
            triggered += 1
            try:
                # Delivery happens before the irreversible ONE_SHOT transition. If the
                # provider fails, the claim is released and retried instead of losing it.
                delivery_result = send_message(_message(alert, price.price))
                print(
                    f"ALERT_DELIVERY_OK ticker={alert.ticker} market={alert.market} "
                    f"price={price.price} result={delivery_result}"
                )
                repo.create_trigger_event(worker_id, [alert.id], alert.ticker, _canonical_market(alert.market),
                    price.price, price.timestamp, price.provider, "BUY_PREBUY_HIGH")
                sent += 1
            except Exception as exc:
                errors += 1
                detail = f"DELIVERY_{type(exc).__name__} ticker={alert.ticker} market={alert.market}: {exc}"
                error_details.append(detail)
                last_error = detail
                print(detail)
                if repo.release_alert(alert.id, worker_id, current + timedelta(minutes=5)):
                    released += 1
        else:
            distance = distance_to_trigger(alert_type=alert.alert_type, price=price.price,
                threshold=alert.threshold, threshold_min=alert.threshold_min,
                threshold_max=alert.threshold_max)
            if repo.release_alert(alert.id, worker_id, next_check_at(current, distance, polling)):
                released += 1

    return LiveResult(
        "LIVE_OK",
        len(claimed),
        checked,
        triggered,
        sent,
        errors,
        released,
        last_error,
        tuple(error_details),
    )
