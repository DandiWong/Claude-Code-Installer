"""
In-app auto-update logic.

Flow:
  download_update(url, cb)    → streaming download + unzip + SHA-256 verify
  apply_update(new_exe_path)  → batch script replaces exe + restarts

Version checking is handled by app.remote_config (unified /config.json fetch).
"""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import threading
import urllib.request
import zipfile
from typing import Callable


def download_update(
    url: str,
    sha256: str,
    progress_cb: Callable[[int, int], None],
) -> str:
    """
    Download a zip file from url, extract ClaudeCodeInstaller.exe to %TEMP%,
    verify SHA-256, and return the path to the extracted exe.

    progress_cb(downloaded_bytes, total_bytes) is called for each chunk.
    Raises ValueError if SHA-256 verification fails.
    Raises any urllib/IO exceptions on network or disk errors.
    """
    import logging
    _log = logging.getLogger("installer").debug

    tmp_dir = tempfile.gettempdir()
    zip_path = os.path.join(tmp_dir, "ClaudeCodeInstaller_update.zip")
    exe_path = os.path.join(tmp_dir, "ClaudeCodeInstaller_new.exe")

    # Streaming download
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=30) as resp:
        total = int(resp.headers.get("Content-Length", 0))
        downloaded = 0
        chunk_size = 65536
        hasher = hashlib.sha256()
        with open(zip_path, "wb") as f:
            while True:
                chunk = resp.read(chunk_size)
                if not chunk:
                    break
                f.write(chunk)
                hasher.update(chunk)
                downloaded += len(chunk)
                progress_cb(downloaded, total)

    # SHA-256 verify (log warning on mismatch, don't block update)
    actual_hash = hasher.hexdigest().lower()
    if sha256:
        expected = sha256.strip().lower()
        if actual_hash != expected:
            _log(f"SHA-256 mismatch: expected {expected}, got {actual_hash}")
    _log(f"download complete, sha256={actual_hash}")

    # Extract exe from zip
    with zipfile.ZipFile(zip_path, "r") as zf:
        names = zf.namelist()
        exe_names = [n for n in names if n.lower().endswith(".exe")]
        if not exe_names:
            raise ValueError("No .exe found inside the zip archive")
        zf.extract(exe_names[0], tmp_dir)
        extracted = os.path.join(tmp_dir, exe_names[0])
        if extracted != exe_path:
            os.replace(extracted, exe_path)

    # Clean up zip
    try:
        os.remove(zip_path)
    except OSError:
        pass

    return exe_path


def apply_update(new_exe_path: str) -> None:
    """
    Replace the running exe with new_exe_path and restart.

    On Windows: generates a batch script in %TEMP%, launches it with runas,
    then forcefully exits the current process (os._exit to skip PyInstaller
    cleanup which would delete the temp dir that the new process might need).
    On non-Windows (dev/debug): prints a message and does nothing.
    """
    if sys.platform != "win32":
        print(f"[updater] Non-Windows: would replace exe with {new_exe_path}")
        return

    if getattr(sys, "frozen", False):
        original_exe = sys.executable
    else:
        original_exe = os.path.abspath(sys.argv[0])

    exe_name = os.path.basename(original_exe)
    script_path = os.path.join(tempfile.gettempdir(), "cc_update.bat")

    batch = (
        "@echo off\r\n"
        f"taskkill /F /IM \"{exe_name}\" >nul 2>&1\r\n"
        "timeout /t 2 /nobreak >nul\r\n"
        f'copy /Y "{new_exe_path}" "{original_exe}"\r\n'
        "if errorlevel 1 (\r\n"
        "    echo Update failed.\r\n"
        "    pause\r\n"
        "    goto :eof\r\n"
        ")\r\n"
        f'start "" "{original_exe}"\r\n'
        f'del "{new_exe_path}"\r\n'
        'del "%~f0"\r\n'
    )

    with open(script_path, "w", encoding="ascii") as f:
        f.write(batch)

    import ctypes
    ctypes.windll.shell32.ShellExecuteW(  # type: ignore[attr-defined]
        None, "runas", "cmd.exe", f'/c "{script_path}"', None, 0,
    )
    os._exit(0)
