from datetime import datetime, timedelta, timezone
from decimal import Decimal

from alert_platform.market_data import MarketPrice, validate_market_price


def make_price(*, age_seconds=0, value="100.00", tz_aware=True):
    now = datetime.now(timezone.utc)
    ts = now - timedelta(seconds=age_seconds)
    if not tz_aware:
        ts = ts.replace(tzinfo=None)
    return MarketPrice(
        ticker="TSM",
        price=Decimal(value),
        timestamp=ts,
        market_status="OPEN",
        provider="TEST",
    ), now


def test_valid_price_is_check_ok():
    price, now = make_price(age_seconds=30)
    ok, code = validate_market_price(price, max_price_age_seconds=120, now=now)
    assert ok is True
    assert code == "CHECK_OK"


def test_stale_price_is_rejected():
    price, now = make_price(age_seconds=121)
    ok, code = validate_market_price(price, max_price_age_seconds=120, now=now)
    assert ok is False
    assert code == "DATA_STALE"


def test_naive_timestamp_is_rejected():
    price, now = make_price(age_seconds=0, tz_aware=False)
    ok, code = validate_market_price(price, max_price_age_seconds=120, now=now)
    assert ok is False
    assert code == "DATA_STALE"


def test_non_positive_price_is_rejected():
    price, now = make_price(value="0")
    ok, code = validate_market_price(price, max_price_age_seconds=120, now=now)
    assert ok is False
    assert code == "PROVIDER_ERROR"


def test_missing_price_is_provider_error():
    ok, code = validate_market_price(None, max_price_age_seconds=120)
    assert ok is False
    assert code == "PROVIDER_ERROR"
