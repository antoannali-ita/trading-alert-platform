import io
import json
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


def test_deduplicates_symbols_and_parses_prices():
    payloads = {
        "TSM": {"price": "414.82"},
        "AAPL": {"price": "230.10"},
    }

    def fake_urlopen(url, timeout):
        symbol = "TSM" if "TSM" in url else "AAPL"
        return FakeResponse(payloads[symbol])

    provider = TwelveDataProvider("secret")
    with patch("alert_platform.providers.twelve_data.urlopen", side_effect=fake_urlopen):
        prices = provider.get_prices(["tsm", "TSM", "AAPL"])

    assert [p.ticker for p in prices] == ["TSM", "AAPL"]
    assert str(prices[0].price) == "414.82"
    assert prices[0].provider == "TWELVE_DATA"


def test_provider_error_is_normalized():
    provider = TwelveDataProvider("secret")
    with patch(
        "alert_platform.providers.twelve_data.urlopen",
        return_value=FakeResponse({"status": "error", "message": "quota exceeded"}),
    ):
        try:
            provider.get_prices(["TSM"])
        except TwelveDataError as exc:
            assert "quota exceeded" in str(exc)
        else:
            raise AssertionError("provider error not raised")


def test_api_key_required():
    try:
        TwelveDataProvider("")
    except ValueError:
        pass
    else:
        raise AssertionError("empty API key should be rejected")
