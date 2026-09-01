from datetime import datetime, timezone

from alert_platform.market_calendar import ExchangeMarketCalendar


def test_us_labor_day_is_not_session():
    cal = ExchangeMarketCalendar()
    assert cal.session_for("USA", datetime(2026, 9, 7, 15, 0, tzinfo=timezone.utc)) is None


def test_us_dst_divergence_open_is_1330_utc():
    cal = ExchangeMarketCalendar()
    window = cal.session_for("USA", datetime(2026, 3, 23, 15, 0, tzinfo=timezone.utc))
    assert window is not None
    assert (window.opened_at.hour, window.opened_at.minute) == (13, 30)
    assert window.refresh_due_at.minute == 33


def test_italy_before_europe_dst_open_is_0800_utc():
    cal = ExchangeMarketCalendar()
    window = cal.session_for("ITALIA", datetime(2026, 3, 23, 10, 0, tzinfo=timezone.utc))
    assert window is not None
    assert (window.opened_at.hour, window.opened_at.minute) == (8, 0)
    assert window.refresh_due_at.minute == 3


def test_italy_after_europe_dst_open_is_0700_utc():
    cal = ExchangeMarketCalendar()
    window = cal.session_for("ITALIA", datetime(2026, 3, 30, 10, 0, tzinfo=timezone.utc))
    assert window is not None
    assert (window.opened_at.hour, window.opened_at.minute) == (7, 0)
