"""
Claude Code Installer — entry point.

On Windows: checks for administrator privileges and re-launches with UAC
elevation if needed. Then opens the GUI window.
"""

import logging
import os
import sys
import traceback
from datetime import datetime


def _config_dir() -> str:
    """Return directory where config and logs are stored (next to executable)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _read_config_ini() -> dict[str, str]:
    """Read config.ini next to the executable as key=value pairs."""
    cfg_path = os.path.join(_config_dir(), "config.ini")
    result: dict[str, str] = {}
    if not os.path.isfile(cfg_path):
        return result
    try:
        with open(cfg_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    key, _, val = line.partition("=")
                    result[key.strip().lower()] = val.strip()
    except Exception:
        pass
    return result


def _is_logging_enabled() -> bool:
    return _read_config_ini().get("logging") == "1"


def setup_error_log() -> logging.Logger:
    """Set up file logger. Only writes when config.ini has logging=1."""
    logger = logging.getLogger("installer")
    logger.setLevel(logging.DEBUG)

    if not _is_logging_enabled():
        # Attach a NullHandler so _log.log() calls don't warn about no handlers
        logger.addHandler(logging.NullHandler())
        return logger

    log_dir = _config_dir()
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_path = os.path.join(log_dir, f"{timestamp}.log")

    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fmt = logging.Formatter("[%(asctime)s] %(levelname)s %(message)s", datefmt="%H:%M:%S")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    logger.info("Log started: %s", log_path)
    return logger


def is_admin() -> bool:
    """Return True if the current process has administrator privileges."""
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def elevate() -> None:
    """Re-launch this script/exe with administrator privileges via UAC."""
    import ctypes

    if getattr(sys, "frozen", False):
        exe = sys.executable
        params = " ".join(f'"{a}"' for a in sys.argv[1:])
    else:
        exe = sys.executable
        params = " ".join(f'"{a}"' for a in sys.argv)

    ctypes.windll.shell32.ShellExecuteW(
        None,       # hwnd
        "runas",    # verb — triggers UAC dialog
        exe,
        params,
        None,       # working directory (use current)
        1,          # SW_SHOWNORMAL
    )
    sys.exit(0)


def _set_dpi_awareness() -> None:
    """Enable Per-Monitor DPI awareness so Windows uses native icon resolution."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 (-4), Windows 10 1703+
        ctypes.windll.user32.SetProcessDpiAwarenessContext(-4)
    except Exception:
        try:
            import ctypes
            # Fallback: PROCESS_PER_MONITOR_DPI_AWARE (2), Windows 8.1+
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass


def main() -> None:
    logger = setup_error_log()

    # Read config.ini for test mode
    if _read_config_ini().get("mode") == "test":
        import app.constants
        app.constants.TEST_MODE = True
        logger.info("Test mode enabled via config.ini")

    # On Windows, ensure we have admin rights
    if sys.platform == "win32" and not is_admin():
        elevate()
        return

    _set_dpi_awareness()

    try:
        from app.window import InstallerWindow
        app = InstallerWindow()
        app.mainloop()
    except Exception:
        logger.critical("Unhandled exception:\n%s", traceback.format_exc())
        # Also write to stderr in case console is available
        traceback.print_exc()


if __name__ == "__main__":
    main()
