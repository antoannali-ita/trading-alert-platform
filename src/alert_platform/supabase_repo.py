"""Supabase REST/RPC adapter for the alert-platform worker."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Sequence
from uuid import UUID

from .worker import ClaimedAlert


@dataclass(frozen=True)
class SupabaseSettings:
    url: str
    secret_key: str

    def validate(self) -> None:
        if not self.url.strip():
            raise ValueError("SUPABASE_URL is required")
        if not self.secret_key.strip():
            raise ValueError("SUPABASE_SECRET_KEY is required")


class SupabaseAlertRepository:
    def __init__(self, settings: SupabaseSettings, *, timeout: int = 20):
        settings.validate()
        self.settings = settings
        self.timeout = timeout

    def _request(self, path: str, payload: dict) -> object:
        base = self.settings.url.rstrip("/")
        url = f"{base}/rest/v1/rpc/{path}"
        body = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "apikey": self.settings.secret_key,
            "Authorization": f"Bearer {self.settings.secret_key}",
            "Accept-Profile": "alert_platform",
            "Content-Profile": "alert_platform",
            "User-Agent": "trading-alert-platform-worker/0.1",
        }
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Supabase RPC {path} failed: HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Supabase RPC {path} transport error: {exc}") from exc
        return json.loads(raw) if raw else None

    @staticmethod
    def _decimal(value: object) -> Decimal | None:
        return None if value is None else Decimal(str(value))

    def claim_due_alerts(self, worker_id: UUID, limit: int) -> Sequence[ClaimedAlert]:
        data = self._request("claim_due_alerts", {"p_worker_id": str(worker_id), "p_limit": int(limit)})
        rows = data if isinstance(data, list) else []
        return tuple(
            ClaimedAlert(
                id=UUID(row["id"]),
                ticker=row["ticker"],
                market=row["market"],
                alert_type=row["alert_type"],
                threshold=self._decimal(row.get("threshold")),
                threshold_min=self._decimal(row.get("threshold_min")),
                threshold_max=self._decimal(row.get("threshold_max")),
                valid_until=datetime.fromisoformat(row["valid_until"].replace("Z", "+00:00")),
                next_check_at=(datetime.fromisoformat(row["next_check_at"].replace("Z", "+00:00")) if row.get("next_check_at") else None),
            )
            for row in rows
        )

    def release_alert(self, alert_id: UUID, worker_id: UUID, next_check_at: datetime) -> bool:
        data = self._request(
            "release_alert",
            {"p_alert_id": str(alert_id), "p_worker_id": str(worker_id), "p_next_check_at": next_check_at.isoformat()},
        )
        return bool(data)

    def record_alert_run(
        self,
        *,
        worker_id: UUID,
        alert_id: UUID,
        ticker: str,
        price: Decimal | None,
        price_timestamp: datetime | None,
        provider: str | None,
        trigger_hit: bool | None,
        error_code: str | None,
        duration_ms: int | None = None,
    ) -> int:
        data = self._request(
            "record_alert_run",
            {
                "p_worker_id": str(worker_id),
                "p_alert_id": str(alert_id),
                "p_ticker": ticker,
                "p_price": str(price) if price is not None else None,
                "p_price_timestamp": price_timestamp.isoformat() if price_timestamp else None,
                "p_provider": provider,
                "p_trigger_hit": trigger_hit,
                "p_error_code": error_code,
                "p_duration_ms": duration_ms,
            },
        )
        row = data[0] if isinstance(data, list) and data else data
        if not isinstance(row, dict):
            return 0
        return int(row.get("retry_count", 0))
