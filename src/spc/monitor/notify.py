"""Notifications of out-of-control conditions (draft 11.1: inform the people concerned promptly).

The program always keeps the incident and shows it on the monitor page. A notifier additionally sends the
event somewhere else. `WebhookNotifier` posts the event as JSON to a URL that the administrator sets when the
server starts (never a URL that a user can type). Sending runs in its own thread, so a slow receiver cannot
hold up the entry of a measurement, and a failure is logged and never raised.
"""

from __future__ import annotations

import json
import logging
import threading
import urllib.request
from typing import Protocol

log = logging.getLogger("spc.monitor")


class Notifier(Protocol):
    def send(self, event: dict) -> None: ...


class ListNotifier:
    """Keeps the events in a list. For tests and for a quick look."""

    def __init__(self) -> None:
        self.events: list[dict] = []

    def send(self, event: dict) -> None:
        self.events.append(event)


class WebhookNotifier:
    def __init__(self, url: str, timeout: float = 5.0):
        if not url.lower().startswith(("http://", "https://")):
            raise ValueError("the alert webhook must be an http or https URL")
        self.url, self.timeout = url, timeout

    def send(self, event: dict) -> None:
        threading.Thread(target=self._post, args=(event,), daemon=True).start()

    def _post(self, event: dict) -> None:
        try:
            request = urllib.request.Request(self.url, data=json.dumps(event).encode("utf-8"), method="POST",
                                             headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(request, timeout=self.timeout):  # noqa: S310 - the URL comes from the server's own settings
                pass
        except Exception as exc:  # a notification must never break the work at the line
            log.warning("alert webhook failed: %s", exc)
