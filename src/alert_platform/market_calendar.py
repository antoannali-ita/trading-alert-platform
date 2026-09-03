"""Exchange-aware regular-session calendar for Italy and USA."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import exchange_calendars as xcals


@dataclass(frozen=True)
class SessionWindow:
    market: str
    session_date: date
    opened_at: datetime
    closed_at: datetime
    refresh_due_at: datetime


class ExchangeMarketCalendar:
    """Uses exchange-calendars for holidays, early closes and DST.

    USA uses XNYS as the shared regular-session gate. NYSE/Nasdaq regular hours
    and US market holidays/early-close behavior are aligned for this purpose.
    """

    CALENDAR_BY_MARKET = {
        "USA": "XNYS",
        "ITALIA": "XMIL",
    }

    MARKET_ALIASES = {
        "ITALY": "ITALIA",
        "ITALIA": "ITALIA",
        "USA": "USA",
    }

    def __init__(self, *, refresh_delay_minutes: int = 3):
        self.refresh_delay_minutes = refresh_delay_minutes
        self._calendars = {
            market: xcals.get_calendar(code)
            for market, code in self.CALENDAR_BY_MARKET.items()
        }

    @classmethod
    def _market_key(cls, market: str) -> str:
        raw = str(market or "").upper().strip()
        return cls.MARKET_ALIASES.get(raw, raw)

    def session_for(self, market: str, now: datetime) -> SessionWindow | None:
        if now.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        key = self._market_key(market)
        calendar = self._calendars.get(key)
        if calendar is None:
            raise ValueError(f"unsupported market: {market}")

        session_label = calendar.minute_to_session(now, direction="none") if calendar.is_open_on_minute(now) else None
        if session_label is None:
            # A session can be known even before/after the bell on the same UTC date.
            candidate = now.date()
            try:
                if calendar.is_session(candidate):
                    session_label = candidate
            except Exception:
                session_label = None
        if session_label is None:
            return None

        opened = calendar.session_open(session_label).to_pydatetime().astimezone(timezone.utc)
        closed = calendar.session_close(session_label).to_pydatetime().astimezone(timezone.utc)
        return SessionWindow(
            market=key,
            session_date=opened.date(),
            opened_at=opened,
            closed_at=closed,
            refresh_due_at=opened + timedelta(minutes=self.refresh_delay_minutes),
        )

    def any_relevant_market_open(self, now: datetime) -> bool:
        if now.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        return any(cal.is_open_on_minute(now) for cal in self._calendars.values())

    def is_market_open(self, market: str, now: datetime) -> bool:
        if now.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        key = self._market_key(market)
        calendar = self._calendars.get(key)
        if calendar is None:
            return False
        return bool(calendar.is_open_on_minute(now))
