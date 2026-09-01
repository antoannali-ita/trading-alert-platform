from decimal import Decimal

from alert_platform.gap_detection import classify_gap


def test_pending_open_when_open_missing():
    result = classify_gap(
        previous_close=Decimal("100"),
        open_price=None,
        current_price=Decimal("101"),
    )
    assert result.flags == ("PENDING_OPEN",)
    assert result.gap_pct is None


def test_gap_through_trigger_and_buy_zone():
    result = classify_gap(
        previous_close=Decimal("104"),
        open_price=Decimal("99"),
        current_price=Decimal("99.5"),
        trigger=Decimal("100"),
        entry_min=Decimal("98"),
        entry_max=Decimal("100"),
        max_buy=Decimal("101"),
    )
    assert "GAP_THROUGH_TRIGGER" in result.flags
    assert "GAP_IN_BUY_ZONE" in result.flags
    assert result.no_chase is False


def test_gap_above_max_buy_sets_no_chase():
    result = classify_gap(
        previous_close=Decimal("100"),
        open_price=Decimal("106"),
        current_price=Decimal("107"),
        trigger=Decimal("102"),
        max_buy=Decimal("105"),
    )
    assert "GAP_EXTREME" in result.flags
    assert "GAP_THROUGH_TRIGGER" in result.flags
    assert "GAP_ABOVE_MAX_BUY" in result.flags
    assert result.no_chase is True
