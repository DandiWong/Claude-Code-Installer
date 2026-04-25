"""
Windows registry helpers. All operations require administrator privileges.

Gracefully handles ImportError so that non-Windows environments can import
this module without crashing (useful for syntax checks on macOS).
"""

from __future__ import annotations
import ctypes
import os
import subprocess

try:
    import winreg

    _WINREG_AVAILABLE = True
except ImportError:
    _WINREG_AVAILABLE = False


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _require_winreg() -> None:
    if not _WINREG_AVAILABLE:
        raise RuntimeError("winreg is only available on Windows")


def _resolve_icon_path() -> str:
    """Return the path where the app icon is (or will be) stored."""
    local_app = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
    return os.path.join(local_app, "ClaudeCode", "icon.ico")


def _wt_command(folder_var: str) -> str:
    """
    Return the best shell command for the context menu entry.
    Falls back to PowerShell if Windows Terminal is not installed.
    """
    from app.checks import check_windows_terminal
    from app.constants import CMD_WT_DIR, CMD_WT_BG, CMD_PS_DIR, CMD_PS_BG

    wt_ok, _ = check_windows_terminal()
    if folder_var == "%V":
        return CMD_WT_DIR if wt_ok else CMD_PS_DIR
    return CMD_WT_BG if wt_ok else CMD_PS_BG


# ---------------------------------------------------------------------------
# Context menu
# ---------------------------------------------------------------------------


def write_context_menu_entries() -> None:
    """
    Create the 'Open Claude Code here' entries under HKEY_LOCAL_MACHINE
    for both folder right-click and folder-background right-click.
    """
    _require_winreg()
    from app.constants import (
        REG_DIR_SHELL,
        REG_DIR_SHELL_CMD,
        REG_BG_SHELL,
        REG_BG_SHELL_CMD,
        MENU_LABEL,
    )

    icon_path = _resolve_icon_path()
    # Copy icon to %LOCALAPPDATA%\ClaudeCode\ if the exe is bundled
    _ensure_icon(icon_path)

    for shell_key, cmd_key, folder_var in (
        (REG_DIR_SHELL, REG_DIR_SHELL_CMD, "%V"),
        (REG_BG_SHELL, REG_BG_SHELL_CMD, "%W"),
    ):
        # Create / open the shell subkey
        key = winreg.CreateKeyEx(
            winreg.HKEY_LOCAL_MACHINE,
            shell_key,
            0,
            winreg.KEY_SET_VALUE,
        )
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, MENU_LABEL)
        if os.path.isfile(icon_path):
            winreg.SetValueEx(key, "Icon", 0, winreg.REG_SZ, icon_path)
        winreg.CloseKey(key)

        # Create / open the command subkey
        cmd_key_handle = winreg.CreateKeyEx(
            winreg.HKEY_LOCAL_MACHINE,
            cmd_key,
            0,
            winreg.KEY_SET_VALUE,
        )
        winreg.SetValueEx(cmd_key_handle, "", 0, winreg.REG_SZ, _wt_command(folder_var))
        winreg.CloseKey(cmd_key_handle)


def remove_context_menu_entries() -> None:
    """Remove all Claude Code context menu registry entries."""
    _require_winreg()
    from app.constants import (
        REG_DIR_SHELL,
        REG_DIR_SHELL_CMD,
        REG_BG_SHELL,
        REG_BG_SHELL_CMD,
    )

    for cmd_key, parent_key in (
        (REG_DIR_SHELL_CMD, REG_DIR_SHELL),
        (REG_BG_SHELL_CMD, REG_BG_SHELL),
    ):
        try:
            winreg.DeleteKey(winreg.HKEY_LOCAL_MACHINE, cmd_key)
        except FileNotFoundError:
            pass
        try:
            winreg.DeleteKey(winreg.HKEY_LOCAL_MACHINE, parent_key)
        except FileNotFoundError:
            pass


# ---------------------------------------------------------------------------
# Windows 11 classic context menu
# ---------------------------------------------------------------------------


def set_classic_context_menu(enable: bool) -> None:
    """
    Enable or disable the Windows 11 'classic' right-click context menu by
    writing/removing the CLSID InprocServer32 key under HKCU.
    """
    _require_winreg()
    from app.constants import REG_CLASSIC_CLSID

    if enable:
        key = winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER,
            REG_CLASSIC_CLSID,
            0,
            winreg.KEY_SET_VALUE,
        )
        # Empty default value enables the classic menu
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "")
        winreg.CloseKey(key)
    else:
        # Remove the key to restore Windows 11 menu
        clsid_parent = REG_CLASSIC_CLSID.rsplit("\\", 2)[0]  # strip \InprocServer32
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, REG_CLASSIC_CLSID)
        except FileNotFoundError:
            pass
        try:
            # Also remove parent CLSID key if it exists and is now empty
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, clsid_parent)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# System PATH
# ---------------------------------------------------------------------------


def get_system_path() -> str:
    """Return the current system PATH value from the registry."""
    _require_winreg()
    from app.constants import REG_SYSTEM_ENV

    key = winreg.OpenKey(
        winreg.HKEY_LOCAL_MACHINE,
        REG_SYSTEM_ENV,
        0,
        winreg.KEY_READ,
    )
    value, _ = winreg.QueryValueEx(key, "Path")
    winreg.CloseKey(key)
    return value


def add_to_system_path(directory: str) -> bool:
    """
    Append directory to the system PATH if not already present.
    Returns True if a change was made.
    """
    _require_winreg()
    from app.constants import REG_SYSTEM_ENV

    current = get_system_path()
    entries = [e.strip() for e in current.split(";") if e.strip()]

    if any(e.lower() == directory.lower() for e in entries):
        return False  # already present

    entries.append(directory)
    new_path = ";".join(entries)

    key = winreg.OpenKey(
        winreg.HKEY_LOCAL_MACHINE,
        REG_SYSTEM_ENV,
        0,
        winreg.KEY_SET_VALUE,
    )
    winreg.SetValueEx(key, "Path", 0, winreg.REG_EXPAND_SZ, new_path)
    winreg.CloseKey(key)

    # Keep current process in sync
    os.environ["PATH"] = os.environ.get("PATH", "") + os.pathsep + directory

    broadcast_env_change()
    return True


def remove_from_system_path(directory: str) -> bool:
    """
    Remove directory from the system PATH if present (case-insensitive).
    Returns True if a change was made.
    """
    _require_winreg()
    from app.constants import REG_SYSTEM_ENV

    current = get_system_path()
    entries = [e.strip() for e in current.split(";") if e.strip()]
    filtered = [e for e in entries if e.lower() != directory.lower()]

    if len(filtered) == len(entries):
        return False  # not found, nothing to do

    new_path = ";".join(filtered)
    key = winreg.OpenKey(
        winreg.HKEY_LOCAL_MACHINE,
        REG_SYSTEM_ENV,
        0,
        winreg.KEY_SET_VALUE,
    )
    winreg.SetValueEx(key, "Path", 0, winreg.REG_EXPAND_SZ, new_path)
    winreg.CloseKey(key)

    # Keep current process in sync so in-process checks reflect the change
    env_entries = [e for e in os.environ.get("PATH", "").split(os.pathsep) if e]
    os.environ["PATH"] = os.pathsep.join(
        e for e in env_entries if e.lower() != directory.lower()
    )

    broadcast_env_change()
    return True


def broadcast_env_change() -> None:
    """
    Notify all top-level windows that the environment has changed so that
    new processes inherit the updated PATH without requiring a reboot.
    """
    HWND_BROADCAST = 0xFFFF
    WM_SETTINGCHANGE = 0x001A
    SMTO_ABORTIFHUNG = 0x0002

    result = ctypes.c_long()
    ctypes.windll.user32.SendMessageTimeoutW(
        HWND_BROADCAST,
        WM_SETTINGCHANGE,
        0,
        "Environment",
        SMTO_ABORTIFHUNG,
        5000,
        ctypes.byref(result),
    )


# ---------------------------------------------------------------------------
# Explorer restart
# ---------------------------------------------------------------------------


def restart_explorer() -> None:
    """Kill and restart Windows Explorer to refresh the context menu."""
    subprocess.run(
        ["taskkill", "/F", "/IM", "explorer.exe"],
        capture_output=True,
    )
    subprocess.Popen(["explorer.exe"])


# ---------------------------------------------------------------------------
# Icon helper
# ---------------------------------------------------------------------------


def _ensure_icon(dest_path: str) -> None:
    """
    Copy the bundled icon from the executable's location to dest_path,
    creating parent directories as needed.
    """
    import shutil
    import sys

    os.makedirs(os.path.dirname(dest_path), exist_ok=True)

    # Resolve icon source path in both dev mode and PyInstaller one-file mode.
    if getattr(sys, "frozen", False):
        candidates = []
        meipass = getattr(sys, "_MEIPASS", None)
        if isinstance(meipass, str) and meipass:
            candidates.append(meipass)
        candidates.append(os.path.dirname(sys.executable))
    else:
        candidates = [os.path.dirname(os.path.dirname(os.path.abspath(__file__)))]

    src = ""
    for base in candidates:
        for rel in (os.path.join("assets", "icon.ico"), "icon.ico"):
            candidate = os.path.join(base, rel)
            if os.path.isfile(candidate):
                src = candidate
                break
        if src:
            break

    if not src:
        return

    # Always refresh the cached icon copy so icon updates in app/assets/icon.ico
    # are picked up on subsequent installs/repairs.
    try:
        shutil.copy2(src, dest_path)
    except OSError:
        pass
