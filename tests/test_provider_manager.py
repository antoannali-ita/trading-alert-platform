from datetime import datetime, timezone
from decimal import Decimal

from alert_platform.market_data import MarketPrice
from alert_platform.provider_manager import ProviderManager


class FakeProvider:
    def __init__(self, prices=None, error=None):
        self.prices = tuple(prices or ())
        self.error = error
        self.calls = []

    def get_prices(self, symbols):
        self.calls.append(tuple(symbols))
        if self.error:
            raise self.error
        wanted = set(symbols)
        return tuple(p for p in self.prices if p.ticker in wanted)


class FakeBudget:
    def __init__(self, allowed):
        self.allowed = allowed
        self.calls = []

    def reserve(self, provider, requested):
        self.calls.append((provider, requested))
        return min(self.allowed, requested)


def price(ticker, provider, quality="PRIMARY_OK"):
    return MarketPrice(
        ticker=ticker,
        price=Decimal("100"),
        timestamp=datetime.now(timezone.utc),
        market_status="OPEN",
        provider=provider,
        data_quality=quality,
    )


def test_primary_only_when_complete():
    primary = FakeProvider([price("AAPL", "TWELVE_DATA")])
    fallback = FakeProvider([price("AAPL", "YAHOO", "FALLBACK_OK")])
    manager = ProviderManager(primary, fallback)

    result = manager.get_prices(["AAPL"])

    assert result[0].provider == "TWELVE_DATA"
    assert fallback.calls == []


def test_fallback_used_for_missing_symbol():
    primary = FakeProvider([price("AAPL", "TWELVE_DATA")])
    fallback = FakeProvider([price("TSM", "YAHOO", "FALLBACK_OK")])
    manager = ProviderManager(primary, fallback)

    result = manager.get_prices(["AAPL", "TSM"])

    assert [p.provider for p in result] == ["TWELVE_DATA", "YAHOO"]
    assert result[1].data_quality == "FALLBACK_OK"
    assert fallback.calls == [("TSM",)]


def test_fallback_used_when_primary_fails():
    primary = FakeProvider(error=RuntimeError("quota"))
    fallback = FakeProvider([price("TSM", "YAHOO", "FALLBACK_OK")])
    manager = ProviderManager(primary, fallback)

    result = manager.get_prices(["TSM"])

    assert len(result) == 1
    assert result[0].provider == "YAHOO"


def test_credit_budget_overflow_goes_to_fallback():
    primary = FakeProvider([
        price("AAPL", "TWELVE_DATA"),
        price("TSM", "TWELVE_DATA"),
    ])
    fallback = FakeProvider([price("TSM", "YAHOO", "FALLBACK_OK")])
    budget = FakeBudget(allowed=1)
    manager = ProviderManager(primary, fallback, budget=budget)

    result = manager.get_prices(["AAPL", "TSM"])

    assert budget.calls == [("TWELVE_DATA", 2)]
    assert primary.calls == [("AAPL",)]
    assert fallback.calls == [("TSM",)]
    assert [p.provider for p in result] == ["TWELVE_DATA", "YAHOO"]
