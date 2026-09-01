from datetime import date, datetime, timedelta, timezone
from uuid import UUID

from alert_platform.scheduled_worker import run_scheduled_shadow_cycle
from alert_platform.session_refresh import ClaimedSession
from alert_platform.worker import WorkerConfig


class Window:
    def __init__(self, market, now):
        self.market=market
        self.session_date=date(2026,9,1)
        self.opened_at=now-timedelta(minutes=10)
        self.closed_at=now+timedelta(hours=4)
        self.refresh_due_at=now-timedelta(minutes=7)


class FakeCalendar:
    def __init__(self, now): self.now=now
    def session_for(self, market, now):
        return Window(market, now) if market=="USA" else None
    def any_relevant_market_open(self, now): return True


class FakeRepo:
    def __init__(self, now):
        self.now=now
        self.excluded=[]
    def ensure_market_session(self, market, session_date, opened_at, refresh_due_at):
        return ClaimedSession(UUID("00000000-0000-0000-0000-000000000801"),market,session_date,opened_at,refresh_due_at,None,"PENDING")
    def claim_session_refresh(self, market, session_date, worker_id):
        return ClaimedSession(UUID("00000000-0000-0000-0000-000000000801"),market,session_date,self.now-timedelta(minutes=10),self.now-timedelta(minutes=7),self.now,"CLAIMED")
    def seed_session_refresh_items(self, session_id, worker_id): return 0
    def claim_session_refresh_items(self, session_id, worker_id, limit): return ()
    def session_refresh_pending_count(self, session_id, worker_id): return 0
    def complete_session_refresh(self, session_id, worker_id, result): return True
    def expire_session_refresh_if_needed(self, session_id, worker_id): return False
    def claim_due_alerts_excluding_markets(self, worker_id, limit, excluded_markets):
        self.excluded.append(tuple(excluded_markets)); return ()
    def claim_due_alerts(self, worker_id, limit): raise AssertionError("market-exclusion claim expected")


class FakeProvider:
    def get_prices(self, symbols): return ()


def test_due_us_refresh_blocks_only_us_from_adaptive_claim():
    now=datetime(2026,9,1,15,0,tzinfo=timezone.utc)
    repo=FakeRepo(now)
    result=run_scheduled_shadow_cycle(
        repo,
        calendar=FakeCalendar(now),
        market_data=FakeProvider(),
        worker_config=WorkerConfig(enable_market_data=True),
        now=now,
    )
    assert result.blocked_markets == ("USA",)
    assert repo.excluded == [("USA",)]
    assert result.refresh_results[0].status == "COMPLETED"
