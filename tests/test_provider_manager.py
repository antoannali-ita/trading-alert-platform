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
        return self.prices


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
