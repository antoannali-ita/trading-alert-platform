"""Five-minute scheduled SHADOW orchestrator.

Session refresh has priority only for the market being refreshed. Other open
markets continue through adaptive polling.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from .market_calendar import ExchangeMarketCalendar
from .provider_manager import ProviderManager
from .session_refresh import SessionRefreshConfig, SessionRefreshResult, run_session_refresh_batch
from .worker import WorkerConfig, WorkerResult, run_shadow_cycle


@dataclass(frozen=True)
class ScheduledResult:
    refresh_results: tuple[SessionRefreshResult, ...]
    adaptive_result: WorkerResult
    blocked_markets: tuple[str, ...]


def run_scheduled_shadow_cycle(
    repo,
    *,
    calendar: ExchangeMarketCalendar,
    market_data: ProviderManager,
    worker_config: WorkerConfig = WorkerConfig(enable_market_data=True),
    refresh_config: SessionRefreshConfig = SessionRefreshConfig(),
    now: datetime | None = None,
) -> ScheduledResult:
    current = now or datetime.now(timezone.utc)
    blocked: list[str] = []
    refresh_results: list[SessionRefreshResult] = []

    for market in ("ITALIA", "USA"):
        window = calendar.session_for(market, current)
        if window is None or not (window.opened_at <= current <= window.closed_at):
            continue

        state = repo.ensure_market_session(market, window.session_date, window.opened_at, window.refresh_due_at)
        if current < window.refresh_due_at:
            continue
        if state.status in ("COMPLETED", "COMPLETED_WITH_PENDING", "FAILED"):
            continue

        blocked.append(market)
        worker_id = uuid4()
        claimed = repo.claim_session_refresh(market, window.session_date, worker_id)
        if claimed is None:
            continue

        result = run_session_refresh_batch(
            repo,
            session=claimed,
            worker_id=worker_id,
            market_data=market_data,
            config=refresh_config,
            now=current,
        )
        refresh_results.append(result)

    adaptive_config = WorkerConfig(
        claim_limit=worker_config.claim_limit,
        enable_market_data=worker_config.enable_market_data,
        enable_auto_trigger=False,
        enable_v3=False,
        send_whatsapp=False,
        max_price_age_seconds=worker_config.max_price_age_seconds,
        polling=worker_config.polling,
        excluded_markets=tuple(blocked),
    )
    adaptive = run_shadow_cycle(
        repo,
        calendar,
        adaptive_config,
        market_data=market_data,
        now=current,
    )
    return ScheduledResult(tuple(refresh_results), adaptive, tuple(blocked))
