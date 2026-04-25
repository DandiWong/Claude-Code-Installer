"""
Loads provider presets from providers.json.
This file is designed to be edited manually to add/modify providers.
"""

from __future__ import annotations
import json
import os


def _get_providers_path() -> str:
    """Return the path to providers.json."""
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "app", "providers.json")


def get_providers() -> list[dict]:
    """Return the list of provider configurations."""
    import sys
    path = _get_providers_path()
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data.get("providers", [])
    except (json.JSONDecodeError, OSError):
        return []


def get_provider_by_name(name: str) -> dict | None:
    """Return a provider config by name, or None if not found."""
    providers = get_providers()
    for p in providers:
        if p.get("name") == name:
            return p
    return None
