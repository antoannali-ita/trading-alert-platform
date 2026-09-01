"""Adaptive polling schedule from Trading Alert Platform spec v1.2 FINAL."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal


@dataclass(frozen=True)
class PollingConfig:
    distance_near: Decimal = Decimal("0.02")
    distance_medium: Decimal = Decimal("0.05")
    check_near_minutes: int = 5
    check_medium_minutes: int = 15
    check_far_minutes: int = 30


def distance_to_trigger(
    *,
    alert_type: str,
    price: Decimal,
    threshold: Decimal | None,
    threshold_min: Decimal | None,
    threshold_max: Decimal | None,
) -> Decimal:
    if price <= 0:
        raise ValueError("price must be > 0")

    if alert_type in {"PRICE_BELOW", "PRICE_ABOVE", "MAX_BUY"}:
        if threshold is None or threshold <= 0:
            raise ValueError(f"threshold required for {alert_type}")
        return abs(price - threshold) / threshold

    if alert_type == "ENTRY_ZONE":
        if threshold_min is None or threshold_max is None:
            raise ValueError("threshold_min and threshold_max required for ENTRY_ZONE")
        if threshold_min <= 0 or threshold_max <= 0 or threshold_min > threshold_max:
            raise ValueError("invalid ENTRY_ZONE thresholds")
        if threshold_min <= price <= threshold_max:
            return Decimal("0")
        boundary = threshold_min if price < threshold_min else threshold_max
        return abs(price - boundary) / boundary

    raise ValueError(f"unsupported operational alert_type: {alert_type}")


def next_check_at(
    now: datetime,
    distance: Decimal,
    config: PollingConfig = PollingConfig(),
) -> datetime:
    if distance < 0:
        raise ValueError("distance must be >= 0")
    if distance <= config.distance_near:
        minutes = config.check_near_minutes
    elif distance <= config.distance_medium:
        minutes = config.check_medium_minutes
    else:
        minutes = config.check_far_minutes
    return now + timedelta(minutes=minutes)
