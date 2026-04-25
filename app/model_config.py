"""
Model configuration step (Step 6) — detection and configuration dialog.
"""

from __future__ import annotations
import urllib.request
import urllib.error
import json as _json

from app.config_manager import is_configured, get_model_config


def check_model_config() -> tuple[bool, str]:
    """Return (is_configured, detail_string)."""
    if is_configured():
        config = get_model_config()
        detail = config.get("PROVIDER_NAME", "") or "已配置"
        return True, detail
    return False, "未配置"


def fetch_models(base_url: str, api_key: str) -> list[str]:
    """
    Fetch available model IDs from the API's /v1/models endpoint.
    Returns a sorted list of model ID strings, or empty list on failure.
    """
    url = base_url.rstrip("/") + "/v1/models"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    try:
        req = urllib.request.Request(url, headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = _json.loads(resp.read())
        # Anthropic format: {"data": [{"id": "model-name", ...}, ...]}
        models = []
        for item in data.get("data", []):
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                models.append(item["id"])
        return sorted(models)
    except Exception:
        return []


def auto_assign_models(models: list[str]) -> tuple[str, str, str]:
    """
    Given a list of model IDs, return (sonnet, opus, haiku) assignments.
    Matches by keyword in model name; falls back to first model for all.
    """
    default = models[0] if models else ""
    sonnet = _find_best(models, ["sonnet"]) or default
    opus = _find_best(models, ["opus"]) or default
    haiku = _find_best(models, ["haiku"]) or default
    return sonnet, opus, haiku


def _find_best(models: list[str], keywords: list[str]) -> str:
    """Find first model whose name contains any keyword (case-insensitive)."""
    for m in models:
        lower = m.lower()
        for kw in keywords:
            if kw in lower:
                return m
    return ""


def test_connection(
    base_url: str,
    api_key: str,
    model_id: str,
    test_path: str = "/v1/messages",
) -> tuple[bool, str]:
    """
    Test the API connection with the given credentials via POST.
    Returns (success, message).
    """
    if not base_url or not api_key:
        return False, "Base URL 和 API Key 为必填项。"

    # Normalize URL
    url = base_url.rstrip("/")
    if not url:
        return False, "无效的 Base URL。"

    if test_path and not test_path.startswith("/"):
        test_path = "/" + test_path
    test_url = f"{url}{test_path}"

    # Determine model - use provided or a known safe fallback
    model = model_id.strip() if model_id else "MiniMax-M2.7"

    payload = _json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 5,
    }).encode("utf-8")

    # Two auth strategies: Anthropic native (x-api-key) and Bearer token
    auth_strategies = [
        {"x-api-key": api_key, "anthropic-version": "2023-06-01"},
        {"Authorization": f"Bearer {api_key}", "anthropic-version": "2023-06-01"},
    ]

    last_error = ""
    for auth_headers in auth_strategies:
        headers = {"Content-Type": "application/json", "User-Agent": "ClaudeCodeInstaller/1.0", **auth_headers}
        try:
            req = urllib.request.Request(test_url, data=payload, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=15) as resp:
                if resp.status == 200:
                    return True, "连接成功！"
                return False, f"HTTP {resp.status}: {resp.reason}"
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")[:200]
            last_error = f"HTTP {e.code}: {e.reason} — {body}"
            if e.code not in (401, 403):
                return False, last_error
            # 401 → try next auth strategy
        except urllib.error.URLError as e:
            return False, f"连接失败: {e.reason}"
        except Exception as e:
            return False, f"错误: {e}"

    return False, last_error
