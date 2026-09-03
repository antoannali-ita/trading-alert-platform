import io
from unittest.mock import patch

from alert_platform.whatsapp import WhatsAppError, send_callmebot


class FakeResponse:
    def __init__(self, body: str, status: int = 200):
        self.status = status
        self._buffer = io.BytesIO(body.encode("utf-8"))

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self._buffer.read()


def test_missing_credentials_are_rejected():
    try:
        send_callmebot("", "key", "hello")
    except WhatsAppError:
        pass
    else:
        raise AssertionError("missing phone should be rejected")


def test_queued_response_is_reported_as_queued():
    with patch("alert_platform.whatsapp.urlopen", return_value=FakeResponse("Message queued. You can leave.")):
        assert send_callmebot("123", "key", "hello") == "PROVIDER_QUEUED"


def test_unconfirmed_response_is_reported_as_unconfirmed():
    with patch("alert_platform.whatsapp.urlopen", return_value=FakeResponse("Message to: 123 sent.")):
        assert send_callmebot("123", "key", "hello") == "PROVIDER_ACCEPTED_UNCONFIRMED"


def test_quota_exhausted_response_is_raised_as_error():
    body = (
        "You have 0 messages left. Please subscribe to continue using CallMeBot."
        "<b>Message not sent</b>"
    )
    with patch("alert_platform.whatsapp.urlopen", return_value=FakeResponse(body)):
        try:
            send_callmebot("123", "key", "hello")
        except WhatsAppError as exc:
            assert "message not sent" in str(exc).lower()
        else:
            raise AssertionError("quota-exhausted response should be rejected, not treated as accepted")
