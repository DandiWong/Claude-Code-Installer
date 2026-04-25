"""
Remote config: two-phase fetch strategy.

Phase 1 — startup, fetch /info.json (encrypted):
    { "latest": {...}, "min_version": "...", "notice": "...", "notice_level": "...", "features": {...} }
    → version check, min_version enforcement, notice banner, feature flags

Phase 2 — during installation, pre-fetch /config.json (encrypted):
    { "providers": [...], "claude_settings": {...} }
    → provider presets for model config dialog
    → default settings merged into ~/.claude/settings.json after model config saved

Phase 2 starts automatically when Phase 1 succeeds (network is available).

Public API:
    fetch_info(on_version_ready)  — call once at startup
    get_providers()               — list[dict], empty if not yet fetched
    get_claude_settings()         — dict, empty if not yet fetched
    get_notice()                  — (text, level) tuple
    is_report_enabled()           — bool (default True)
"""

from __future__ import annotations

import json
import threading
import urllib.request
from typing import Callable

_info_cache: dict | None = None
_config_cache: dict | None = None
_config_fetching: bool = False
_lock = threading.Lock()

import logging
_log = logging.getLogger("installer").debug


# ── Phase 1: /info.json ───────────────────────────────────────────

def fetch_info(on_version_ready: Callable[[dict | None], None]) -> None:
    """
    Fetch /info.json in a background daemon thread.
    On success: caches info, triggers Phase 2 pre-fetch, then calls on_version_ready.
    on_version_ready receives latest dict if update available (with _forced=True when
    below min_version), or None otherwise.
    Never raises.
    """
    def _worker() -> None:
        global _info_cache
        try:
            from app.constants import get_info_url, APP_VERSION
            from app.crypto import verify_and_decrypt

            _log(f"APP_VERSION={APP_VERSION}, fetching {get_info_url()}")
            req = urllib.request.Request(get_info_url(), method="GET")
            req.add_header("User-Agent", f"ClaudeCodeInstaller/{APP_VERSION}")
            with urllib.request.urlopen(req, timeout=8) as resp:
                payload = json.loads(resp.read())

            _log(f"payload keys={list(payload.keys())}")
            plaintext = verify_and_decrypt(payload)
            if not plaintext:
                _log("verify_and_decrypt returned None")
                on_version_ready(None)
                return

            info = json.loads(plaintext)
            _log(f"info={json.dumps(info, ensure_ascii=False)[:300]}")
            with _lock:
                _info_cache = info

            # Network is available — kick off Phase 2 in background
            threading.Thread(target=_fetch_config_worker, daemon=True).start()

            _check_version(info, on_version_ready)
        except Exception as e:
            _log(f"fetch_info exception: {e}")
            on_version_ready(None)

    threading.Thread(target=_worker, daemon=True).start()


# ── Phase 2: /config.json ─────────────────────────────────────────

def _fetch_config_worker() -> None:
    """Background worker: fetch /config.json and cache providers + claude_settings."""
    global _config_cache, _config_fetching
    with _lock:
        _config_fetching = True
    try:
        from app.constants import get_config_url, APP_VERSION
        from app.crypto import verify_and_decrypt

        req = urllib.request.Request(get_config_url(), method="GET")
        req.add_header("User-Agent", f"ClaudeCodeInstaller/{APP_VERSION}")
        with urllib.request.urlopen(req, timeout=15) as resp:
            payload = json.loads(resp.read())

        plaintext = verify_and_decrypt(payload)
        if not plaintext:
            return

        data = json.loads(plaintext)
        with _lock:
            _config_cache = data
    except Exception:
        pass
    finally:
        with _lock:
            _config_fetching = False


# ── Version check ─────────────────────────────────────────────────

def _check_version(info: dict, callback: Callable[[dict | None], None]) -> None:
    """
    Compare remote latest.version with local APP_VERSION.
    Forces update callback if local version is below min_version.
    """
    try:
        from app.constants import APP_VERSION

        def _parse(v: str) -> tuple[int, ...]:
            try:
                return tuple(int(x) for x in str(v).strip().split("."))
            except (ValueError, AttributeError):
                return (0,)

        latest = info.get("latest", {})
        remote_ver = latest.get("version", "")
        min_ver = info.get("min_version", "")
        local = _parse(APP_VERSION)

        _log(f"version check: remote={remote_ver}, min={min_ver}, local={APP_VERSION} → parsed: {local} vs {_parse(remote_ver)}")

        if min_ver and _parse(min_ver) > local:
            _log("result: FORCED update (below min_version)")
            callback({**latest, "_forced": True})
            return

        if remote_ver and _parse(remote_ver) > local:
            _log("result: update available")
            callback(latest)
        else:
            _log("result: no update")
            callback(None)
    except Exception as e:
        _log(f"_check_version exception: {e}")
        callback(None)


# ── Public accessors ──────────────────────────────────────────────

def is_config_loading() -> bool:
    """True while Phase 2 config fetch is still in progress."""
    with _lock:
        return _config_fetching


def get_providers() -> list[dict]:
    """Return cached provider list, or [] if config not yet fetched."""
    with _lock:
        if _config_cache is None:
            return []
        result = _config_cache.get("providers", [])
    return result if isinstance(result, list) else []


def get_claude_settings() -> dict:
    """Return cached claude_settings, or {} if config not yet fetched."""
    with _lock:
        if _config_cache is None:
            return {}
        result = _config_cache.get("claude_settings", {})
    return result if isinstance(result, dict) else {}


def get_notice() -> tuple[str, str]:
    """Return (notice_text, notice_level). Level: 'info' | 'warning' | 'error'."""
    with _lock:
        if _info_cache is None:
            return "", "info"
        text = _info_cache.get("notice", "").strip()
        level = _info_cache.get("notice_level", "info")
    return text, level


def is_report_enabled() -> bool:
    """Return whether usage reporting is enabled (default True)."""
    with _lock:
        if _info_cache is None:
            return True
        enabled = _info_cache.get("features", {}).get("report_enabled", True)
    return bool(enabled)
