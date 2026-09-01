from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from alert_platform.market_data import MarketPrice
from alert_platform.worker import ClaimedAlert, WorkerConfig, run_shadow_cycle


class FakeRepo:
    def __init__(self, alerts):
        self.alerts = alerts
        self.claim_calls = []
        self.release_calls = []

    def claim_due_alerts(self, worker_id, limit):
        self.claim_calls.append((worker_id, limit))
        return self.alerts

    def release_alert(self, alert_id, worker_id, next_check_at):
        self.release_calls.append((alert_id, worker_id, next_check_at))
        return True


class FakeMarketHours:
    def __init__(self, is_open):
        self.is_open = is_open

    def any_relevant_market_open(self, now):
        return self.is_open


class FakeMarketData:
    def __init__(self, prices):
        self.prices = prices
        self.calls = []

    def get_prices(self, symbols):
        self.calls.append(tuple(symbols))
        return self.prices


def make_alert(ticker="TSM", suffix="1", threshold="100"):
    now = datetime.now(timezone.utc)
    return ClaimedAlert(
        id=UUID(f"00000000-0000-0000-0000-00000000000{suffix}"),
        ticker=ticker,
        market="USA",
        alert_type="PRICE_BELOW",
        threshold=Decimal(threshold),
        threshold_min=None,
        threshold_max=None,
        valid_until=now + timedelta(days=1),
        next_check_at=now,
    )


def test_market_closed_does_not_claim():
    repo = FakeRepo([make_alert()])
    result = run_shadow_cycle(repo, FakeMarketHours(False), WorkerConfig())
    assert result.status == "MARKET_CLOSED"
    assert repo.claim_calls == []


def test_shadow_claims_when_market_open_without_market_data():
    repo = FakeRepo([make_alert()])
    result = run_shadow_cycle(repo, FakeMarketHours(True), WorkerConfig(claim_limit=25))
    assert result.status == "SHADOW_OK"
    assert result.claimed == 1
    assert repo.claim_calls[0][1] == 25


def test_shadow_rejects_trigger_v3_and_whatsapp():
    repo = FakeRepo([])
    for config in (
        WorkerConfig(enable_auto_trigger=True),
        WorkerConfig(enable_v3=True),
        WorkerConfig(send_whatsapp=True),
    ):
        try:
            run_shadow_cycle(repo, FakeMarketHours(True), config)
        except RuntimeError as exc:
            assert "Shadow worker" in str(exc)
        else:
            raise AssertionError("live capability guardrail did not fire")


def test_market_data_shadow_deduplicates_and_releases_adaptively():
    now = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
    repo = FakeRepo([
        make_alert("TSM", "1", "100"),
        make_alert("TSM", "2", "200"),
        make_alert("AAPL", "3", "220"),
    ])
    provider = FakeMarketData([
        MarketPrice("TSM", Decimal("101"), now - timedelta(seconds=10), "OPEN", "TWELVE_DATA"),
        MarketPrice("AAPL", Decimal("220"), now - timedelta(seconds=300), "OPEN", "TWELVE_DATA"),
    ])

    result = run_shadow_cycle(
        repo,
        FakeMarketHours(True),
        WorkerConfig(enable_market_data=True, max_price_age_seconds=120),
        market_data=provider,
        now=now,
    )

    assert result.status == "SHADOW_MARKET_DATA_OK"
    assert result.claimed == 3
    assert result.unique_tickers == 2
    assert result.valid_prices == 2  # two TSM alerts share one valid ticker price
    assert result.invalid_prices == 1
    assert result.released == 3
    assert provider.calls == [("TSM", "AAPL")]
    release_times = [call[2] for call in repo.release_calls]
    assert release_times[0] == now + timedelta(minutes=5)   # TSM 1% from 100
    assert release_times[1] == now + timedelta(minutes=30)  # TSM far from 200
    assert release_times[2] == now + timedelta(minutes=1)   # stale AAPL retry


def test_market_data_provider_required_when_enabled():
    repo = FakeRepo([make_alert()])
    try:
        run_shadow_cycle(repo, FakeMarketHours(True), WorkerConfig(enable_market_data=True))
    except RuntimeError as exc:
        assert "market_data provider is required" in str(exc)
    else:
        raise AssertionError("missing provider guardrail did not fire")
