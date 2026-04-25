"""
Resolve latest downloadable installer artifacts for system dependencies.
Always resolves x64 binaries.
"""

from __future__ import annotations

from typing import Any

import requests


_REQUEST_TIMEOUT = 15
_USER_AGENT = "ClaudeCodeInstaller/1.0"


def _get_json(url: str, headers: dict[str, str] | None = None) -> Any:
    req_headers = {"User-Agent": _USER_AGENT}
    if headers:
        req_headers.update(headers)
    response = requests.get(url, timeout=_REQUEST_TIMEOUT, headers=req_headers)
    response.raise_for_status()
    return response.json()


def latest_node_lts_x64_msi() -> tuple[str, list[str]]:
    """Return (filename, urls) for latest Node.js LTS Windows x64 MSI."""
    index_sources = [
        "https://npmmirror.com/mirrors/node/index.json",
        "https://nodejs.org/dist/index.json",
    ]

    last_exc: Exception | None = None
    for index_url in index_sources:
        try:
            payload = _get_json(index_url)
            if not isinstance(payload, list):
                continue

            for item in payload:
                if not isinstance(item, dict):
                    continue
                version = item.get("version")
                lts = item.get("lts")
                files = item.get("files")

                if not isinstance(version, str) or not version.startswith("v"):
                    continue
                if not lts:
                    continue
                if not isinstance(files, list) or "win-x64-msi" not in files:
                    continue

                filename = f"node-{version}-x64.msi"
                urls = [
                    f"https://npmmirror.com/mirrors/node/{version}/{filename}",
                    f"https://nodejs.org/dist/{version}/{filename}",
                ]
                return filename, urls
        except Exception as exc:
            last_exc = exc

    if last_exc is not None:
        raise RuntimeError(f"could not resolve latest Node.js LTS ({last_exc})")
    raise RuntimeError("could not resolve latest Node.js LTS")


def latest_git_windows_x64_exe() -> tuple[str, list[str]]:
    """Return (filename, urls) for latest Git for Windows x64 installer exe."""
    release = _get_json(
        "https://api.github.com/repos/git-for-windows/git/releases/latest",
        headers={"Accept": "application/vnd.github+json"},
    )
    assets = release.get("assets") if isinstance(release, dict) else None
    if not isinstance(assets, list):
        raise RuntimeError("unexpected GitHub release payload")

    for asset in assets:
        if not isinstance(asset, dict):
            continue
        name = asset.get("name")
        download_url = asset.get("browser_download_url")
        if not isinstance(name, str) or not isinstance(download_url, str):
            continue
        if not name.startswith("Git-") or not name.endswith("-64-bit.exe"):
            continue

        urls = [
            f"https://mirrors.tuna.tsinghua.edu.cn/github-release/git-for-windows/git/LatestRelease/{name}",
            download_url,
        ]
        return name, urls

    raise RuntimeError("latest release does not contain Git x64 installer asset")
