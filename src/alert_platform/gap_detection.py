"""Pure gap/session-open classification helpers."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class GapThresholds:
    minor: Decimal = Decimal("0.01")
    material: Decimal = Decimal("0.02")
    large: Decimal = Decimal("0.03")
    extreme: Decimal = Decimal("0.05")


@dataclass(frozen=True)
class GapResult:
    gap_pct: Decimal | None
    flags: tuple[str, ...]
    no_chase: bool


def classify_gap(
    *,
    previous_close: Decimal | None,
    open_price: Decimal | None,
    current_price: Decimal,
    trigger: Decimal | None = None,
    entry_min: Decimal | None = None,
    entry_max: Decimal | None = None,
    max_buy: Decimal | None = None,
    thresholds: GapThresholds = GapThresholds(),
) -> GapResult:
    if previous_close is None or previous_close <= 0 or open_price is None or open_price <= 0:
        return GapResult(None, ("PENDING_OPEN",), False)

    gap_pct = (open_price - previous_close) / previous_close
    absolute_gap = abs(gap_pct)
    flags: list[str] = []

    if absolute_gap >= thresholds.extreme:
        flags.append("GAP_EXTREME")
    elif absolute_gap >= thresholds.large:
        flags.append("GAP_LARGE")
    elif absolute_gap >= thresholds.material:
        flags.append("GAP_MATERIAL")
    elif absolute_gap >= thresholds.minor:
        flags.append("GAP_MINOR")
    else:
        flags.append("NORMAL_OPEN")

    if trigger is not None:
        before = previous_close - trigger
        after = open_price - trigger
        if before != 0 and after != 0 and (before > 0) != (after > 0):
            flags.append("GAP_THROUGH_TRIGGER")
        elif abs(after) < abs(before):
            flags.append("GAP_TOWARD_TRIGGER")
        elif abs(after) > abs(before):
            flags.append("GAP_AWAY_FROM_TRIGGER")

    if entry_min is not None and entry_max is not None:
        if entry_min <= current_price <= entry_max:
            flags.append("GAP_IN_BUY_ZONE")

    no_chase = bool(max_buy is not None and current_price > max_buy)
    if no_chase:
        flags.append("GAP_ABOVE_MAX_BUY")

    return GapResult(gap_pct, tuple(dict.fromkeys(flags)), no_chase)
