from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from alert_platform.market_data import MarketPrice
from alert_platform.worker import ClaimedAlert, WorkerConfig, run_shadow_cycle


class FakeRepo:
    def __init__(self, alerts):
        self.alerts = alerts
        self.calls = []

    def claim_due_alerts(self, worker_id, limit):
        self.calls.append((worker_id, limit))
        return self.alerts


class FakeMarketHours:
    def __init__(self, is_open):
        self.is_open = is_open
        self.calls = []

    def any_relevant_market_open(self, now):
        self.calls.append(now)
        return self.is_open


class FakeMarketData:
    def __init__(self, prices):
        self.prices = prices
        self.calls = []

    def get_prices(self, symbols):
        self.calls.append(tuple(symbols))
        return self.prices


def make_alert(ticker="TSM", suffix="1"):
    now = datetime.now(timezone.utc)
    return ClaimedAlert(
        id=UUID(f"00000000-0000-0000-0000-00000000000{suffix}"),
        ticker=ticker,
        market="USA",
        valid_until=now + timedelta(days=1),
        next_check_at=now,
    )


def test_market_closed_does_not_claim():
    repo = FakeRepo([make_alert()])
    market = FakeMarketHours(False)

    result = run_shadow_cycle(repo, market, WorkerConfig())

    assert result.status == "MARKET_CLOSED"
    assert result.claimed == 0
    assert repo.calls == []


def test_shadow_claims_when_market_open():
    repo = FakeRepo([make_alert()])
    market = FakeMarketHours(True)

    result = run_shadow_cycle(repo, market, WorkerConfig(claim_limit=25))

    assert result.status == "SHADOW_OK"
    assert result.claimed == 1
    assert result.unique_tickers == 1
    assert len(repo.calls) == 1
    assert repo.calls[0][1] == 25


def test_shadow_rejects_trigger_v3_and_whatsapp():
    repo = FakeRepo([])
    market = FakeMarketHours(True)

    for config in (
        WorkerConfig(enable_auto_trigger=True),
        WorkerConfig(enable_v3=True),
        WorkerConfig(send_whatsapp=True),
    ):
        try:
            run_shadow_cycle(repo, market, config)
        except RuntimeError as exc:
            assert "Shadow worker" in str(exc)
        else:
            raise AssertionError("live capability guardrail did not fire")


def test_market_data_shadow_deduplicates_tickers_and_validates_prices():
    now = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
    repo = FakeRepo([
        make_alert("TSM", "1"),
        make_alert("TSM", "2"),
        make_alert("AAPL", "3"),
    ])
    market = FakeMarketHours(True)
    provider = FakeMarketData([
        MarketPrice(
            ticker="TSM",
            price=Decimal("250.00"),
            timestamp=now - timedelta(seconds=10),
            market_status="OPEN",
            provider="TWELVE_DATA",
        ),
        MarketPrice(
            ticker="AAPL",
            price=Decimal("220.00"),
            timestamp=now - timedelta(seconds=300),
            market_status="OPEN",
            provider="TWELVE_DATA",
        ),
    ])

    result = run_shadow_cycle(
        repo,
        market,
        WorkerConfig(enable_market_data=True, max_price_age_seconds=120),
        market_data=provider,
        now=now,
    )

    assert result.status == "SHADOW_MARKET_DATA_OK"
    assert result.claimed == 3
    assert result.unique_tickers == 2
    assert result.valid_prices == 1
    assert result.invalid_prices == 1
    assert provider.calls == [("TSM", "AAPL")]


def test_market_data_provider_required_when_enabled():
    repo = FakeRepo([make_alert()])
    market = FakeMarketHours(True)

    try:
        run_shadow_cycle(
            repo,
            market,
            WorkerConfig(enable_market_data=True),
        )
    except RuntimeError as exc:
        assert "market_data provider is required" in str(exc)
    else:
        raise AssertionError("missing provider guardrail did not fire")
