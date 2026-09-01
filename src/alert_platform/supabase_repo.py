"""Supabase REST/RPC adapter for the alert-platform worker."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Sequence
from uuid import UUID

from .market_data import MarketPrice
from .session_refresh import ClaimedSession, RefreshItem
from .worker import ClaimedAlert


@dataclass(frozen=True)
class SupabaseSettings:
    url: str
    secret_key: str

    def validate(self) -> None:
        if not self.url.strip(): raise ValueError("SUPABASE_URL is required")
        if not self.secret_key.strip(): raise ValueError("SUPABASE_SECRET_KEY is required")


class SupabaseAlertRepository:
    def __init__(self, settings: SupabaseSettings, *, timeout: int = 20):
        settings.validate(); self.settings = settings; self.timeout = timeout

    def _request(self, path: str, payload: dict) -> object:
        base = self.settings.url.rstrip("/")
        url = f"{base}/rest/v1/rpc/{path}"
        body = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type":"application/json","apikey":self.settings.secret_key,
                   "Authorization":f"Bearer {self.settings.secret_key}","Accept-Profile":"alert_platform",
                   "Content-Profile":"alert_platform","User-Agent":"trading-alert-platform-worker/0.1"}
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp: raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Supabase RPC {path} failed: HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Supabase RPC {path} transport error: {exc}") from exc
        return json.loads(raw) if raw else None

    @staticmethod
    def _decimal(value: object) -> Decimal | None: return None if value is None else Decimal(str(value))
    @staticmethod
    def _dt(value: str | None) -> datetime | None: return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None

    def _parse_alerts(self, data: object) -> Sequence[ClaimedAlert]:
        rows = data if isinstance(data, list) else []
        return tuple(ClaimedAlert(
            id=UUID(row["id"]), ticker=row["ticker"], market=row["market"], alert_type=row["alert_type"],
            threshold=self._decimal(row.get("threshold")), threshold_min=self._decimal(row.get("threshold_min")),
            threshold_max=self._decimal(row.get("threshold_max")), valid_until=self._dt(row["valid_until"]),
            next_check_at=self._dt(row.get("next_check_at")),
        ) for row in rows)

    def _parse_session(self, row: dict) -> ClaimedSession:
        return ClaimedSession(
            id=UUID(row["id"]), market=row["market"], session_date=date.fromisoformat(row["session_date"]),
            opened_at=self._dt(row["opened_at"]), refresh_due_at=self._dt(row["refresh_due_at"]),
            refresh_started_at=self._dt(row.get("refresh_started_at")), status=row["status"],
        )

    def claim_due_alerts(self, worker_id: UUID, limit: int) -> Sequence[ClaimedAlert]:
        return self._parse_alerts(self._request("claim_due_alerts", {"p_worker_id":str(worker_id),"p_limit":int(limit)}))

    def claim_due_alerts_excluding_markets(self, worker_id: UUID, limit: int, excluded_markets: Sequence[str]) -> Sequence[ClaimedAlert]:
        return self._parse_alerts(self._request("claim_due_alerts_excluding_markets", {
            "p_worker_id":str(worker_id),"p_limit":int(limit),"p_excluded_markets":[m.upper() for m in excluded_markets]}))

    def release_alert(self, alert_id: UUID, worker_id: UUID, next_check_at: datetime) -> bool:
        return bool(self._request("release_alert", {"p_alert_id":str(alert_id),"p_worker_id":str(worker_id),"p_next_check_at":next_check_at.isoformat()}))

    def record_alert_run(self, *, worker_id: UUID, alert_id: UUID, ticker: str, price: Decimal | None,
                         price_timestamp: datetime | None, provider: str | None, trigger_hit: bool | None,
                         error_code: str | None, duration_ms: int | None = None) -> int:
        data = self._request("record_alert_run", {"p_worker_id":str(worker_id),"p_alert_id":str(alert_id),"p_ticker":ticker,
            "p_price":str(price) if price is not None else None,"p_price_timestamp":price_timestamp.isoformat() if price_timestamp else None,
            "p_provider":provider,"p_trigger_hit":trigger_hit,"p_error_code":error_code,"p_duration_ms":duration_ms})
        row = data[0] if isinstance(data,list) and data else data
        return int(row.get("retry_count",0)) if isinstance(row,dict) else 0

    def ensure_market_session(self, market: str, session_date: date, opened_at: datetime, refresh_due_at: datetime) -> ClaimedSession:
        data = self._request("ensure_market_session", {"p_market":market,"p_session_date":session_date.isoformat(),
            "p_opened_at":opened_at.isoformat(),"p_refresh_due_at":refresh_due_at.isoformat()})
        row = data[0] if isinstance(data,list) and data else data
        if not isinstance(row,dict): raise RuntimeError("ensure_market_session returned no row")
        return self._parse_session(row)

    def claim_session_refresh(self, market: str, session_date: date, worker_id: UUID) -> ClaimedSession | None:
        data = self._request("claim_session_refresh", {"p_market":market,"p_session_date":session_date.isoformat(),"p_worker_id":str(worker_id)})
        rows = data if isinstance(data,list) else []
        return self._parse_session(rows[0]) if rows else None

    def seed_session_refresh_items(self, session_id: UUID, worker_id: UUID) -> int:
        return int(self._request("seed_session_refresh_items", {"p_session_id":str(session_id),"p_worker_id":str(worker_id)}) or 0)

    def claim_session_refresh_items(self, session_id: UUID, worker_id: UUID, limit: int) -> Sequence[RefreshItem]:
        data = self._request("claim_session_refresh_items", {"p_session_id":str(session_id),"p_worker_id":str(worker_id),"p_limit":int(limit)})
        rows = data if isinstance(data,list) else []
        return tuple(RefreshItem(id=UUID(r["id"]),ticker=r["ticker"],market=r["market"],priority_class=r["priority_class"],status=r["status"],
            retry_count=int(r.get("retry_count",0)),entry_min=self._decimal(r.get("entry_min")),entry_max=self._decimal(r.get("entry_max")),max_buy=self._decimal(r.get("max_buy"))) for r in rows)

    def update_session_refresh_item(self, session_id: UUID, worker_id: UUID, ticker: str, **fields) -> bool:
        payload={"p_session_id":str(session_id),"p_worker_id":str(worker_id),"p_ticker":ticker}
        mapping={"status":"p_status","current_price":"p_current_price","price_timestamp":"p_price_timestamp","open_price":"p_open_price",
                 "previous_close":"p_previous_close","high_price":"p_high_price","low_price":"p_low_price","volume":"p_volume",
                 "provider":"p_provider","data_quality":"p_data_quality","gap_pct":"p_gap_pct","gap_flags":"p_gap_flags",
                 "error_code":"p_error_code","next_retry_at":"p_next_retry_at"}
        for key,rpc_key in mapping.items():
            value=fields.get(key)
            if isinstance(value,Decimal): value=str(value)
            if isinstance(value,datetime): value=value.isoformat()
            payload[rpc_key]=value
        return bool(self._request("update_session_refresh_item",payload))

    def apply_session_price_to_alerts(self, session_id: UUID, worker_id: UUID, ticker: str, price: MarketPrice, next_check: datetime) -> int:
        return int(self._request("apply_session_price_to_alerts", {"p_session_id":str(session_id),"p_worker_id":str(worker_id),"p_ticker":ticker,
            "p_price":str(price.price),"p_price_timestamp":price.timestamp.isoformat(),"p_provider":price.provider,"p_next_check_at":next_check.isoformat()}) or 0)

    def session_refresh_pending_count(self, session_id: UUID, worker_id: UUID) -> int:
        return int(self._request("session_refresh_pending_count", {"p_session_id":str(session_id),"p_worker_id":str(worker_id)}) or 0)
    def complete_session_refresh(self, session_id: UUID, worker_id: UUID, result: dict) -> bool:
        return bool(self._request("complete_session_refresh", {"p_session_id":str(session_id),"p_worker_id":str(worker_id),"p_result":result}))
    def expire_session_refresh_if_needed(self, session_id: UUID, worker_id: UUID) -> bool:
        return bool(self._request("expire_session_refresh_if_needed", {"p_session_id":str(session_id),"p_worker_id":str(worker_id)}))
    def reserve_provider_credits(self, provider: str, requested: int, per_minute_limit: int, daily_budget: int) -> int:
        return int(self._request("reserve_provider_credits", {"p_provider":provider,"p_requested":int(requested),
            "p_per_minute_limit":int(per_minute_limit),"p_daily_budget":int(daily_budget)}) or 0)


class SupabaseCreditBudget:
    def __init__(self, repo: SupabaseAlertRepository, *, per_minute_limit: int = 8, daily_budget: int = 800):
        self.repo=repo; self.per_minute_limit=per_minute_limit; self.daily_budget=daily_budget
    def reserve(self, provider: str, requested: int) -> int:
        return self.repo.reserve_provider_credits(provider,requested,self.per_minute_limit,self.daily_budget)
