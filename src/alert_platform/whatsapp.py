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
                if response.status >= 400 or any(x in body for x in ("error", "invalid", "not authorized", "failed")):
                    raise WhatsAppError(f"CallMeBot rejected request (HTTP {response.status})")
                return "PROVIDER_ACCEPTED"
        except WhatsAppError:
            raise
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(2)
    raise WhatsAppError(f"CallMeBot transport failure: {type(last_error).__name__}") from last_error
