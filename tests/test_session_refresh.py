from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from alert_platform.market_data import MarketPrice
from alert_platform.session_refresh import (
    ClaimedSession,
    RefreshItem,
    SessionRefreshConfig,
    run_session_refresh_batch,
)


class FakeRepo:
    def __init__(self, items):
        self.items = list(items)
        self.updated = []
        self.applied = []
        self.completed = []

    def seed_session_refresh_items(self, session_id, worker_id): return len(self.items)
    def claim_session_refresh_items(self, session_id, worker_id, limit): return tuple(self.items[:limit])
    def update_session_refresh_item(self, session_id, worker_id, ticker, **fields):
        self.updated.append((ticker, fields)); return True
    def apply_session_price_to_alerts(self, session_id, worker_id, ticker, price, next_check):
        self.applied.append((ticker, price, next_check)); return 1
    def session_refresh_pending_count(self, session_id, worker_id):
        terminal = {t for t, f in self.updated if f.get("status") in ("UPDATED","DEGRADED","NO_OPEN_DATA","FAILED")}
        return sum(1 for i in self.items if i.ticker not in terminal)
    def complete_session_refresh(self, session_id, worker_id, result): self.completed.append(result); return True
    def expire_session_refresh_if_needed(self, session_id, worker_id): return True


class FakeProvider:
    def __init__(self, prices): self.prices = prices
    def get_prices(self, symbols): return tuple(self.prices[s] for s in symbols if s in self.prices)


def session(now):
    return ClaimedSession(
        id=UUID("00000000-0000-0000-0000-000000000701"), market="USA", session_date=date(2026,9,1),
        opened_at=now-timedelta(minutes=10), refresh_due_at=now-timedelta(minutes=7),
        refresh_started_at=now-timedelta(minutes=1), status="CLAIMED",
    )


def item(ticker="TSM", retry=0):
    return RefreshItem(
        id=UUID("00000000-0000-0000-0000-000000000702"), ticker=ticker, market="USA",
        priority_class="CRITICAL", status="PROCESSING", retry_count=retry,
        entry_min=Decimal("98"), entry_max=Decimal("100"), max_buy=Decimal("101"),
    )


def quote(now, quality="PRIMARY_OK", with_open=True):
    return MarketPrice(
        ticker="TSM", price=Decimal("99.5"), timestamp=now-timedelta(seconds=5),
        market_status="OPEN", provider="TWELVE_DATA" if quality=="PRIMARY_OK" else "YAHOO",
        data_quality=quality,
        open_price=Decimal("99") if with_open else None,
        previous_close=Decimal("104") if with_open else None,
        high_price=Decimal("100"), low_price=Decimal("98"), volume=Decimal("100000"),
    )


def test_primary_quote_updates_and_applies_price():
    now=datetime(2026,9,1,14,0,tzinfo=timezone.utc)
    repo=FakeRepo([item()])
    result=run_session_refresh_batch(repo,session=session(now),worker_id=UUID("00000000-0000-0000-0000-000000000703"),
        market_data=FakeProvider({"TSM":quote(now)}),now=now)
    assert result.updated==1
    assert repo.updated[0][1]["status"]=="UPDATED"
    assert repo.applied


def test_fallback_quote_is_degraded():
    now=datetime(2026,9,1,14,0,tzinfo=timezone.utc)
    repo=FakeRepo([item()])
    result=run_session_refresh_batch(repo,session=session(now),worker_id=UUID("00000000-0000-0000-0000-000000000703"),
        market_data=FakeProvider({"TSM":quote(now,"FALLBACK_OK")}),now=now)
    assert result.degraded==1
    assert repo.updated[0][1]["status"]=="DEGRADED"


def test_missing_open_retries_then_no_open_data():
    now=datetime(2026,9,1,14,0,tzinfo=timezone.utc)
    repo=FakeRepo([item(retry=2)])
    result=run_session_refresh_batch(repo,session=session(now),worker_id=UUID("00000000-0000-0000-0000-000000000703"),
        market_data=FakeProvider({"TSM":quote(now,with_open=False)}),now=now)
    assert result.no_open_data==1
    assert repo.updated[0][1]["status"]=="NO_OPEN_DATA"
