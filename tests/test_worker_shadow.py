from datetime import datetime, timedelta, timezone
from uuid import UUID

from alert_platform.worker import ClaimedAlert, WorkerConfig, run_shadow_cycle


class FakeRepo:
    def __init__(self, alerts):
        self.alerts = alerts
        self.calls = []

    def claim_due_alerts(self, worker_id, limit):
        self.calls.append((worker_id, limit))
        return self.alerts


class FakeMarketHours:
    def __init__(self, is_open):
        self.is_open = is_open
        self.calls = []

    def any_relevant_market_open(self, now):
        self.calls.append(now)
        return self.is_open


def make_alert():
    now = datetime.now(timezone.utc)
    return ClaimedAlert(
        id=UUID("00000000-0000-0000-0000-000000000001"),
        ticker="TSM",
        market="USA",
        valid_until=now + timedelta(days=1),
        next_check_at=now,
    )


def test_market_closed_does_not_claim():
    repo = FakeRepo([make_alert()])
    market = FakeMarketHours(False)

    result = run_shadow_cycle(repo, market, WorkerConfig())

    assert result.status == "MARKET_CLOSED"
    assert result.claimed == 0
    assert repo.calls == []


def test_shadow_claims_when_market_open():
    repo = FakeRepo([make_alert()])
    market = FakeMarketHours(True)

    result = run_shadow_cycle(repo, market, WorkerConfig(claim_limit=25))

    assert result.status == "SHADOW_OK"
    assert result.claimed == 1
    assert len(repo.calls) == 1
    assert repo.calls[0][1] == 25


def test_shadow_rejects_live_capabilities():
    repo = FakeRepo([])
    market = FakeMarketHours(True)

    try:
        run_shadow_cycle(
            repo,
            market,
            WorkerConfig(enable_market_data=True),
        )
    except RuntimeError as exc:
        assert "Shadow foundation" in str(exc)
    else:
        raise AssertionError("live capability guardrail did not fire")
