"""Audit whether Platform alert thresholds crossed since each alert was created."""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal


def _dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _get_json(url: str, headers: dict[str, str] | None = None) -> object:
    request = urllib.request.Request(url, headers=headers or {"User-Agent": "trading-alert-platform-audit/1.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def load_alerts() -> list[dict]:
    base = os.environ["SUPABASE_URL"].rstrip("/")
    key = os.environ["SUPABASE_SECRET_KEY"]
    select = (
        "id,ticker,market,alert_type,threshold,threshold_min,threshold_max,status,"
        "created_at,valid_until,triggered_at,trigger_price"
    )
    query = urllib.parse.urlencode({"select": select, "order": "created_at.asc", "limit": "3000"})
    data = _get_json(
        f"{base}/rest/v1/alerts?{query}",
        {
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Accept-Profile": "alert_platform",
            "User-Agent": "trading-alert-platform-audit/1.0",
        },
    )
    return data if isinstance(data, list) else []


def yahoo_symbol(ticker: str, market: str) -> str:
    symbol = ticker.strip().upper()
    if market.strip().upper() in {"ITALIA", "ITALY"} and not symbol.endswith(".MI"):
        symbol += ".MI"
    return symbol


def history_extremes(ticker: str, market: str, created_at: datetime) -> tuple[Decimal, Decimal, Decimal]:
    now = datetime.now(timezone.utc)
    params = urllib.parse.urlencode(
        {
            "period1": int(created_at.timestamp()),
            "period2": int(now.timestamp()) + 60,
            "interval": "5m",
            "includePrePost": "false",
            "events": "div,splits",
        }
    )
    symbol = urllib.parse.quote(yahoo_symbol(ticker, market), safe="")
    payload = _get_json(f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?{params}")
    result = payload["chart"]["result"][0]
    quote = result["indicators"]["quote"][0]
    lows = [Decimal(str(x)) for x in quote.get("low", []) if x is not None]
    highs = [Decimal(str(x)) for x in quote.get("high", []) if x is not None]
    closes = [Decimal(str(x)) for x in quote.get("close", []) if x is not None]
    if not lows or not highs or not closes:
        raise RuntimeError("no historical prices")
    return min(lows), max(highs), closes[-1]


def crossed(alert: dict, low: Decimal, high: Decimal) -> bool:
    alert_type = str(alert.get("alert_type") or "").upper()
    threshold = Decimal(str(alert["threshold"])) if alert.get("threshold") is not None else None
    threshold_min = Decimal(str(alert["threshold_min"])) if alert.get("threshold_min") is not None else None
    threshold_max = Decimal(str(alert["threshold_max"])) if alert.get("threshold_max") is not None else None
    if alert_type == "PRICE_ABOVE":
        return threshold is not None and high >= threshold
    if alert_type == "PRICE_BELOW":
        return threshold is not None and low <= threshold
    if alert_type == "ENTRY_ZONE":
        return threshold_min is not None and threshold_max is not None and low <= threshold_max and high >= threshold_min
    if alert_type == "MAX_BUY":
        return threshold is not None and high > threshold
    return False


def main() -> int:
    alerts = load_alerts()
    summary = {"total": len(alerts), "crossed": 0, "not_crossed": 0, "errors": 0}
    print(f"AUDIT_ALERTS total={len(alerts)}")
    for alert in alerts:
        ticker = str(alert.get("ticker") or "").upper()
        market = str(alert.get("market") or "USA").upper()
        try:
            low, high, last = history_extremes(ticker, market, _dt(alert["created_at"]))
            hit = crossed(alert, low, high)
            summary["crossed" if hit else "not_crossed"] += 1
            print(json.dumps({
                "ticker": ticker,
                "market": market,
                "type": alert.get("alert_type"),
                "threshold": alert.get("threshold"),
                "threshold_min": alert.get("threshold_min"),
                "threshold_max": alert.get("threshold_max"),
                "created_at": alert.get("created_at"),
                "status": alert.get("status"),
                "historical_low": str(low),
                "historical_high": str(high),
                "last": str(last),
                "crossed": hit,
            }, separators=(",", ":")))
        except Exception as exc:
            summary["errors"] += 1
            print(json.dumps({"ticker": ticker, "market": market, "error": f"{type(exc).__name__}: {exc}"}, separators=(",", ":")))
    print("AUDIT_SUMMARY " + json.dumps(summary, separators=(",", ":")))
    return 0 if summary["errors"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
