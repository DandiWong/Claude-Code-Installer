"""
Reads and writes Claude Code's settings.json env section.
Handles Windows path conventions and creates the file if missing.
"""

from __future__ import annotations
import json
import os


def _get_claude_dir() -> str:
    """Return the Claude config directory path."""
    userprofile = os.environ.get("USERPROFILE", os.path.expanduser("~"))
    return os.path.join(userprofile, ".claude")


def _get_settings_path() -> str:
    """Return the full path to settings.json."""
    return os.path.join(_get_claude_dir(), "settings.json")


def read_settings() -> dict:
    """
    Read the settings.json file.
    Returns an empty dict if the file doesn't exist or is invalid.
    """
    path = _get_settings_path()
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def write_settings(data: dict) -> None:
    """Write the full settings.json file, creating directories if needed."""
    claude_dir = _get_claude_dir()
    os.makedirs(claude_dir, exist_ok=True)

    path = _get_settings_path()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def get_model_config() -> dict:
    """
    Return the current model configuration from env section.
    Returns empty dict if not configured.
    """
    settings = read_settings()
    return settings.get("env", {})


def save_model_config(
    base_url: str,
    api_key: str,
    sonnet_model: str,
    opus_model: str,
    haiku_model: str,
    provider_name: str = "",
    default_model: str = "",
) -> None:
    """
    Save model configuration to settings.json env section.
    Preserves other env keys and other top-level settings.
    default_model maps to ANTHROPIC_MODEL (the base default for all requests).
    """
    settings = read_settings()

    # Ensure env section exists
    if "env" not in settings:
        settings["env"] = {}

    # Update env fields
    settings["env"]["ANTHROPIC_BASE_URL"] = base_url.rstrip("/")
    settings["env"]["ANTHROPIC_AUTH_TOKEN"] = api_key
    if default_model:
        settings["env"]["ANTHROPIC_MODEL"] = default_model
    if sonnet_model:
        settings["env"]["ANTHROPIC_DEFAULT_SONNET_MODEL"] = sonnet_model
    if opus_model:
        settings["env"]["ANTHROPIC_DEFAULT_OPUS_MODEL"] = opus_model
    if haiku_model:
        settings["env"]["ANTHROPIC_DEFAULT_HAIKU_MODEL"] = haiku_model
    if provider_name:
        settings["env"]["PROVIDER_NAME"] = provider_name

    # Mark onboarding as completed
    settings["hasCompletedOnboarding"] = True

    write_settings(settings)


def is_configured() -> bool:
    """Return True if a model config (API key) is already present."""
    config = get_model_config()
    return bool(config.get("ANTHROPIC_AUTH_TOKEN", "").strip())


def apply_remote_settings(claude_settings: dict) -> None:
    """
    Deep-merge claude_settings from remote config into settings.json.
    Only adds missing keys — never overwrites existing user configuration.
    Silent no-op on any error.
    """
    if not claude_settings:
        return
    try:
        settings = read_settings()

        # Merge env: only add keys not already present
        remote_env = claude_settings.get("env", {})
        if remote_env:
            local_env = settings.setdefault("env", {})
            for key, value in remote_env.items():
                if key not in local_env:
                    local_env[key] = value

        # Merge cleanupPeriodDays: only set if not present
        if "cleanupPeriodDays" in claude_settings and "cleanupPeriodDays" not in settings:
            settings["cleanupPeriodDays"] = claude_settings["cleanupPeriodDays"]

        # Merge permissions: only add missing entries to allow/deny lists
        remote_perms = claude_settings.get("permissions", {})
        if remote_perms:
            local_perms = settings.setdefault("permissions", {})
            for key in ("allow", "deny"):
                if key in remote_perms:
                    local_list = local_perms.setdefault(key, [])
                    existing = set(local_list)
                    for entry in remote_perms[key]:
                        if entry not in existing:
                            local_list.append(entry)
                            existing.add(entry)

        write_settings(settings)
    except Exception:
        pass


def clear_model_config() -> None:
    """Remove model configuration from settings.json."""
    settings = read_settings()
    if "env" in settings:
        settings["env"].pop("ANTHROPIC_BASE_URL", None)
        settings["env"].pop("ANTHROPIC_AUTH_TOKEN", None)
        settings["env"].pop("ANTHROPIC_MODEL", None)
        settings["env"].pop("ANTHROPIC_DEFAULT_SONNET_MODEL", None)
        settings["env"].pop("ANTHROPIC_DEFAULT_OPUS_MODEL", None)
        settings["env"].pop("ANTHROPIC_DEFAULT_HAIKU_MODEL", None)
        # Remove empty env section
        if not settings["env"]:
            del settings["env"]
    settings.pop("hasCompletedOnboarding", None)
    write_settings(settings)
