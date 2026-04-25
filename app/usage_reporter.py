"""
Fire-and-forget usage event reporter.

Reports two events:
- startup : once when the app launches
- success : once when installation completes
"""

from __future__ import annotations

import json
import threading
import urllib.request
from datetime import datetime, timezone

from app.constants import get_report_url


def report_event(event: str, provider: str = "") -> None:
    """Send a usage event to the server (fire-and-forget, best-effort)."""
    payload = json.dumps({
        "event": event,
        "provider": provider,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }).encode("utf-8")

    def _send() -> None:
        try:
            from app import remote_config
            if not remote_config.is_report_enabled():
                return
            req = urllib.request.Request(
                get_report_url(),
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=5)
        except Exception:
            pass  # best-effort, never block the UI

    threading.Thread(target=_send, daemon=True).start()


def report_startup() -> None:
    report_event("startup")


def report_success(provider: str = "") -> None:
    report_event("success", provider)
