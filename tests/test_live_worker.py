from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from alert_platform.live_worker import is_triggered, run_live_cycle
from alert_platform.market_data import MarketPrice
from alert_platform.worker import ClaimedAlert


NOW = datetime(2026, 9, 2, 15, 0, tzinfo=timezone.utc)


class OpenMarket:
    def any_relevant_market_open(self, now): return True


class Provider:
    def get_prices(self, symbols):
        return [MarketPrice(s, Decimal("99"), NOW, "OPEN", "TEST") for s in symbols]


class Repo:
    def __init__(self): self.released = []; self.events = []
    def claim_due_alerts(self, worker, limit):
        return [ClaimedAlert(uuid4(), "ABC", "USA", "PRICE_BELOW", Decimal("100"), None, None,
                             NOW + timedelta(days=1), NOW)]
    def record_alert_run(self, **kwargs): return 0
    def release_alert(self, alert_id, worker, next_at): self.released.append(alert_id); return True
    def create_trigger_event(self, *args): self.events.append(args); return uuid4()


def test_trigger_predicates():
    assert is_triggered("PRICE_BELOW", Decimal("9"), Decimal("10"), None, None)
    assert is_triggered("PRICE_ABOVE", Decimal("11"), Decimal("10"), None, None)
    assert is_triggered("ENTRY_ZONE", Decimal("10"), None, Decimal("9"), Decimal("11"))
    assert not is_triggered("PRICE_ABOVE", Decimal("9"), Decimal("10"), None, None)


def test_live_cycle_sends_and_marks_triggered():
    repo = Repo(); messages = []
    result = run_live_cycle(repo, OpenMarket(), Provider(), lambda msg: messages.append(msg) or "OK", now=NOW)
    assert result.sent == 1 and result.triggered == 1 and result.errors == 0
    assert len(messages) == 1 and "ABC" in messages[0]
    assert len(repo.events) == 1 and not repo.released


def test_delivery_failure_releases_for_retry():
    repo = Repo()
    def fail(_): raise RuntimeError("provider down")
    result = run_live_cycle(repo, OpenMarket(), Provider(), fail, now=NOW)
    assert result.sent == 0 and result.errors == 1 and result.released == 1
    assert not repo.events


def test_delivery_failure_exposes_sanitized_diagnostic():
    repo = Repo()
    def fail(_): raise RuntimeError("provider rejected request")
    result = run_live_cycle(repo, OpenMarket(), Provider(), fail, now=NOW)
    assert result.last_error == "DELIVERY_RuntimeError: provider rejected request"
