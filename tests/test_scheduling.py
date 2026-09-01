from datetime import datetime, timedelta, timezone
from decimal import Decimal

from alert_platform.scheduling import distance_to_trigger, next_check_at


def test_price_threshold_distance_and_poll_buckets():
    now = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)

    near = distance_to_trigger(
        alert_type="PRICE_BELOW", price=Decimal("101"), threshold=Decimal("100"),
        threshold_min=None, threshold_max=None,
    )
    medium = distance_to_trigger(
        alert_type="PRICE_ABOVE", price=Decimal("103"), threshold=Decimal("100"),
        threshold_min=None, threshold_max=None,
    )
    far = distance_to_trigger(
        alert_type="MAX_BUY", price=Decimal("110"), threshold=Decimal("100"),
        threshold_min=None, threshold_max=None,
    )

    assert next_check_at(now, near) == now + timedelta(minutes=5)
    assert next_check_at(now, medium) == now + timedelta(minutes=15)
    assert next_check_at(now, far) == now + timedelta(minutes=30)


def test_entry_zone_distance_is_zero_inside_zone():
    distance = distance_to_trigger(
        alert_type="ENTRY_ZONE",
        price=Decimal("105"),
        threshold=None,
        threshold_min=Decimal("100"),
        threshold_max=Decimal("110"),
    )
    assert distance == 0
