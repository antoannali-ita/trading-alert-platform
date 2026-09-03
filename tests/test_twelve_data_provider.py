import io
import json
from datetime import datetime, timezone
from unittest.mock import patch

from alert_platform.providers.twelve_data import TwelveDataError, TwelveDataProvider


class FakeResponse:
    def __init__(self, payload):
        self._buffer = io.BytesIO(json.dumps(payload).encode("utf-8"))

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self._buffer.read()


def test_deduplicates_symbols_and_parses_quote_timestamp():
    ts = 1788264000
    payloads = {
        "TSM": {"close": "414.82", "timestamp": ts, "is_market_open": True},
        "AAPL": {"close": "230.10", "timestamp": ts, "is_market_open": True},
    }

    def fake_urlopen(request, timeout):
        url = request.full_url
        symbol = "TSM" if "TSM" in url else "AAPL"
        return FakeResponse(payloads[symbol])

    provider = TwelveDataProvider("secret")
    with patch("alert_platform.providers.twelve_data.urlopen", side_effect=fake_urlopen):
        prices = provider.get_prices(["tsm", "TSM", "AAPL"])

    assert [p.ticker for p in prices] == ["TSM", "AAPL"]
    assert str(prices[0].price) == "414.82"
    assert prices[0].provider == "TWELVE_DATA"
    assert prices[0].timestamp == datetime.fromtimestamp(ts, tz=timezone.utc)


def test_get_one_normalizes_provider_error():
    provider = TwelveDataProvider("secret")
    with patch(
        "alert_platform.providers.twelve_data.urlopen",
        return_value=FakeResponse({"status": "error", "message": "quota exceeded"}),
    ):
        try:
            provider._get_one("TSM")
        except TwelveDataError as exc:
            assert "quota exceeded" in str(exc)
        else:
            raise AssertionError("provider error not raised")


def test_get_one_rejects_missing_timestamp():
    provider = TwelveDataProvider("secret")
    with patch(
        "alert_platform.providers.twelve_data.urlopen",
        return_value=FakeResponse({"close": "100.0"}),
    ):
        try:
            provider._get_one("TSM")
        except TwelveDataError as exc:
            assert "no timestamp" in str(exc)
        else:
            raise AssertionError("missing provider timestamp should be rejected")


def test_get_prices_isolates_symbol_error_from_batch(capsys):
    ts = 1788264000
    payloads = {
        "TSM": {"status": "error", "message": "quota exceeded"},
        "AAPL": {"close": "230.10", "timestamp": ts, "is_market_open": True},
    }

    def fake_urlopen(request, timeout):
        symbol = "TSM" if "TSM" in request.full_url else "AAPL"
        return FakeResponse(payloads[symbol])

    provider = TwelveDataProvider("secret")
    with patch("alert_platform.providers.twelve_data.urlopen", side_effect=fake_urlopen):
        prices = provider.get_prices(["TSM", "AAPL"])

    # A single bad symbol must not erase valid quotes for the rest of the batch.
    assert [p.ticker for p in prices] == ["AAPL"]
    assert "MARKET_DATA_SYMBOL_ERROR" in capsys.readouterr().out


def test_api_key_required():
    try:
        TwelveDataProvider("")
    except ValueError:
        pass
    else:
        raise AssertionError("empty API key should be rejected")
