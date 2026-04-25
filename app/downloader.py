"""
Streaming file download with progress callbacks and mirror fallback.
"""

from __future__ import annotations
import os
import tempfile
from typing import Callable

import requests

_DOWNLOAD_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
}


def download_file(
    url: str,
    dest: str,
    progress_cb: Callable[[int, int], None] | None = None,
) -> str:
    """
    Download a single URL to dest path (creates parent dirs as needed).
    progress_cb(downloaded_bytes, total_bytes) is called periodically.
    Returns dest on success. Raises requests.RequestException on failure.
    """
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    response = requests.get(
        url,
        stream=True,
        timeout=60,
        headers=_DOWNLOAD_HEADERS,
    )
    response.raise_for_status()

    total = int(response.headers.get("Content-Length", 0))
    downloaded = 0
    chunk_size = 65536  # 64 KB

    # Write to a temp file first; rename on success to avoid partial files
    tmp_dest = dest + ".part"
    with open(tmp_dest, "wb") as f:
        for chunk in response.iter_content(chunk_size=chunk_size):
            if chunk:
                f.write(chunk)
                downloaded += len(chunk)
                if progress_cb:
                    progress_cb(downloaded, total)

    os.replace(tmp_dest, dest)
    return dest


def download_with_fallback(
    urls: list[str],
    dest: str,
    log_cb: Callable[[str], None] | None = None,
    progress_cb: Callable[[int, int], None] | None = None,
) -> str:
    """
    Try each URL in order, stopping at the first that succeeds.
    log_cb receives status messages (e.g. "Trying mirror …").
    Returns dest on success. Raises the last exception if all URLs fail.
    """
    last_exc: Exception = RuntimeError("No URLs provided")
    for i, url in enumerate(urls):
        label = "mirror" if i == 0 else "official source"
        if log_cb:
            log_cb(f"正在从 {label}: {url} 下载...")
        try:
            return download_file(url, dest, progress_cb)
        except Exception as exc:
            last_exc = exc
            if log_cb:
                log_cb(f"  Failed ({exc}), trying next source…")
            # Remove partial file before retrying
            for path in (dest, dest + ".part"):
                try:
                    os.remove(path)
                except OSError:
                    pass
    raise last_exc


def download_to_temp(
    urls: list[str] | str,
    filename: str,
    log_cb: Callable[[str], None] | None = None,
    progress_cb: Callable[[int, int], None] | None = None,
) -> str:
    """
    Download to %TEMP%/filename with mirror fallback.
    urls can be a single URL string or a list (tried in order).
    Returns full destination path.
    """
    if isinstance(urls, str):
        urls = [urls]
    dest = os.path.join(tempfile.gettempdir(), filename)
    return download_with_fallback(urls, dest, log_cb=log_cb, progress_cb=progress_cb)
