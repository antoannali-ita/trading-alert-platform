"""Shadow worker foundation for Trading Alert Platform v1.2 FINAL.

This module intentionally contains orchestration only. Market data, V3 and
notifications remain disabled until the database/state-machine foundation is green.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol, Sequence
from uuid import UUID, uuid4


@dataclass(frozen=True)
class ClaimedAlert:
    id: UUID
    ticker: str
    market: str
    valid_until: datetime
    next_check_at: datetime | None


@dataclass(frozen=True)
class WorkerConfig:
    claim_limit: int = 100
    enable_market_data: bool = False
    enable_auto_trigger: bool = False
    enable_v3: bool = False
    send_whatsapp: bool = False


class AlertRepository(Protocol):
    def claim_due_alerts(self, worker_id: UUID, limit: int) -> Sequence[ClaimedAlert]: ...


class MarketHours(Protocol):
    def any_relevant_market_open(self, now: datetime) -> bool: ...


@dataclass(frozen=True)
class WorkerResult:
    worker_id: UUID
    status: str
    claimed: int


def run_shadow_cycle(
    repo: AlertRepository,
    market_hours: MarketHours,
    config: WorkerConfig,
    *,
    now: datetime | None = None,
) -> WorkerResult:
    """Run only the claim/scheduling shell in SHADOW mode.

    No market-data lookup, trigger transition, V3 call or outbound notification
    is allowed from this foundation function.
    """

    current_time = now or datetime.now(timezone.utc)
    worker_id = uuid4()

    if not market_hours.any_relevant_market_open(current_time):
        return WorkerResult(worker_id=worker_id, status="MARKET_CLOSED", claimed=0)

    claimed = tuple(repo.claim_due_alerts(worker_id, config.claim_limit))

    # Hard guardrail while foundation is still in shadow-only stage.
    if any(
        (
            config.enable_market_data,
            config.enable_auto_trigger,
            config.enable_v3,
            config.send_whatsapp,
        )
    ):
        raise RuntimeError(
            "Shadow foundation cannot enable market data, auto-trigger, V3 or WhatsApp"
        )

    return WorkerResult(worker_id=worker_id, status="SHADOW_OK", claimed=len(claimed))
