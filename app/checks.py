"""
Dependency detection functions. Pure functions with no side effects.
Each returns (installed: bool, detail: str).
"""

import os
import subprocess
import shutil


def _run(args: list[str]) -> tuple[bool, str]:
    """Run a command and return (success, output)."""
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode == 0, (result.stdout.strip() or result.stderr.strip())
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False, ""


def check_node() -> tuple[bool, str]:
    """Return (True, 'v20.x.x') if Node.js is installed."""
    ok, out = _run(["node", "--version"])
    if ok and out:
        return True, out
    # Also check common install path directly
    prog = os.environ.get("ProgramFiles", r"C:\Program Files")
    node_exe = os.path.join(prog, "nodejs", "node.exe")
    if os.path.isfile(node_exe):
        ok2, out2 = _run([node_exe, "--version"])
        if ok2:
            return True, out2
    return False, "未找到"


def check_npm() -> tuple[bool, str]:
    """Return (True, 'x.x.x') if npm is installed."""
    ok, out = _run(["npm", "--version"])
    if ok and out:
        return True, out
    # Fallback: check npm directly in the Node.js install directory
    npm_cmd = _get_npm_exe()
    if npm_cmd:
        ok2, out2 = _run([npm_cmd, "--version"])
        if ok2:
            return True, out2
    return False, "未找到"


def _get_npm_exe() -> str | None:
    """Return full path to npm.cmd if found in Node.js install dir, else None."""
    prog = os.environ.get("ProgramFiles", r"C:\Program Files")
    npm_cmd = os.path.join(prog, "nodejs", "npm.cmd")
    if os.path.isfile(npm_cmd):
        return npm_cmd
    return None


def get_npm_path() -> str | None:
    """Return the npm executable to use (PATH 'npm' or full path), or None if missing."""
    ok, _ = _run(["npm", "--version"])
    if ok:
        return "npm"
    return _get_npm_exe()


def check_git() -> tuple[bool, str]:
    """Return (True, 'v2.53.0') if Git is installed."""
    ok, out = _run(["git", "--version"])
    if ok and out:
        import re
        m = re.search(r"(\d+\.\d+\.\d+)", out)
        return True, f"v{m.group(1)}" if m else out
    return False, "未找到"


def check_windows_terminal() -> tuple[bool, str]:
    """Return (True, path) if Windows Terminal (wt.exe) is available."""
    # Check via PATH first
    wt = shutil.which("wt")
    if wt:
        return True, wt
    # Check %LOCALAPPDATA%\Microsoft\WindowsApps\wt.exe
    local_app = os.environ.get("LOCALAPPDATA", "")
    candidate = os.path.join(local_app, "Microsoft", "WindowsApps", "wt.exe")
    if os.path.isfile(candidate):
        return True, candidate
    return False, "未找到"


def _read_claude_pkg_version() -> str | None:
    """Read version from Claude Code's installed package.json, or None."""
    appdata = os.environ.get("APPDATA", "")
    pkg_json = os.path.join(
        appdata, "npm", "node_modules", "@anthropic-ai",
        "claude-code", "package.json",
    )
    if not os.path.isfile(pkg_json):
        return None
    try:
        import json
        with open(pkg_json, encoding="utf-8") as f:
            ver = json.load(f).get("version", "")
            return f"v{ver}" if ver else None
    except Exception:
        return None


def check_claude_code() -> tuple[bool, str]:
    """Return (True, version) if Claude Code CLI is installed globally."""
    ok, out = _run(["claude", "--version"])
    if ok and out:
        return True, out
    # Check if the actual npm package directory exists (not just wrapper files)
    appdata = os.environ.get("APPDATA", "")
    pkg_dir = os.path.join(appdata, "npm", "node_modules", "@anthropic-ai", "claude-code")
    if os.path.isdir(pkg_dir):
        ver = _read_claude_pkg_version()
        return True, ver or "已安装"
    return False, "未找到"


def check_path_entries() -> list[str]:
    """
    Return list of directory strings that are missing from the system PATH.
    Expands environment variable placeholders before comparing.
    """
    from app.constants import PATH_NODE_PLACEHOLDER, PATH_NPM_PLACEHOLDER

    current_path = os.environ.get("PATH", "").lower()
    missing: list[str] = []

    for placeholder in (PATH_NODE_PLACEHOLDER, PATH_NPM_PLACEHOLDER):
        expanded = os.path.expandvars(placeholder)
        if expanded.lower() not in current_path:
            missing.append(expanded)

    return missing


def check_context_menu() -> bool:
    """Return True if the Claude Code context menu entry exists in the registry."""
    try:
        import winreg
        from app.constants import REG_DIR_SHELL
        winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, REG_DIR_SHELL, 0, winreg.KEY_READ)
        return True
    except Exception:
        return False
