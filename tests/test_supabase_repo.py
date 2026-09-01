import json
from datetime import datetime, timezone
from uuid import UUID

from alert_platform.supabase_repo import SupabaseAlertRepository, SupabaseSettings


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def test_settings_require_url_and_key():
    try:
        SupabaseSettings("", "x").validate()
    except ValueError as exc:
        assert "SUPABASE_URL" in str(exc)
    else:
        raise AssertionError("missing URL should fail")


def test_claim_parses_rows(monkeypatch):
    row = {
        "id": "00000000-0000-0000-0000-000000000001",
        "ticker": "TSM",
        "market": "USA",
        "alert_type": "PRICE_BELOW",
        "threshold": "410.00",
        "threshold_min": None,
        "threshold_max": None,
        "valid_until": "2026-09-02T12:00:00+00:00",
        "next_check_at": "2026-09-01T12:00:00+00:00",
    }

    def fake_urlopen(req, timeout):
        assert req.full_url.endswith("/rest/v1/rpc/claim_due_alerts")
        return FakeResponse([row])

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    repo = SupabaseAlertRepository(SupabaseSettings("https://example.supabase.co", "secret"))
    alerts = repo.claim_due_alerts(UUID("00000000-0000-0000-0000-000000000099"), 10)

    assert len(alerts) == 1
    assert alerts[0].ticker == "TSM"
    assert alerts[0].alert_type == "PRICE_BELOW"
    assert str(alerts[0].threshold) == "410.00"
    assert alerts[0].next_check_at.tzinfo is not None


def test_release_returns_boolean(monkeypatch):
    def fake_urlopen(req, timeout):
        assert req.full_url.endswith("/rest/v1/rpc/release_alert")
        return FakeResponse(True)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    repo = SupabaseAlertRepository(SupabaseSettings("https://example.supabase.co", "secret"))
    ok = repo.release_alert(
        UUID("00000000-0000-0000-0000-000000000001"),
        UUID("00000000-0000-0000-0000-000000000099"),
        datetime.now(timezone.utc),
    )
    assert ok is True
