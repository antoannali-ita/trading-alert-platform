"""Small, dependency-free CallMeBot WhatsApp client."""

from __future__ import annotations

import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen


class WhatsAppError(RuntimeError):
    pass


def send_callmebot(phone: str, api_key: str, message: str, *, attempts: int = 3) -> str:
    if not phone.strip() or not api_key.strip():
        raise WhatsAppError("WHATSAPP_NUMBER/CALLMEBOT_APIKEY are required")
    url = "https://api.callmebot.com/whatsapp.php?" + urlencode(
        {"phone": phone, "apikey": api_key, "text": message}
    )
    last_error: Exception | None = None
    for attempt in range(1, max(1, attempts) + 1):
        try:
            with urlopen(url, timeout=30) as response:
                body = response.read().decode("utf-8", errors="replace").lower()
                # "Message not sent"/"messages left" cover CallMeBot's free-tier
                # daily quota exhaustion, which is an explicit rejection but does
                # not contain "error"/"invalid"/"not authorized"/"failed".
                if response.status >= 400 or any(
                    x in body for x in (
                        "error", "invalid", "not authorized", "failed",
                        "message not sent", "messages left",
                    )
                ):
                    raise WhatsAppError(f"CallMeBot rejected request (HTTP {response.status}): {body[:200]}")
                # CallMeBot acknowledges asynchronous submission with "Message
                # queued".  Do not call that a delivered message: the provider
                # can accept the HTTP request and still fail to deliver it to
                # WhatsApp (for example after authorization expires).
                if "message queued" in body:
                    return "PROVIDER_QUEUED"
                # Some CallMeBot responses are HTTP 200 without the historical
                # queue acknowledgement. Keep processing other alerts, but make
                # the weaker transport outcome visible to operations.
                return "PROVIDER_ACCEPTED_UNCONFIRMED"
        except WhatsAppError:
            raise
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(2)
    raise WhatsAppError(f"CallMeBot transport failure: {type(last_error).__name__}") from last_error
