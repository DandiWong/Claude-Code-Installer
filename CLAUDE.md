# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A Python GUI installer for setting up Claude Code on Windows. Built with CustomTkinter, packaged as a single `.exe` via PyInstaller. Requires administrator privileges (UAC elevation).

**Language:** Python 3.11
**UI Framework:** CustomTkinter + Pillow
**Packaging:** PyInstaller (build.spec)
**CI:** GitHub Actions — builds `ClaudeCodeInstaller.exe` on `windows-latest` on push to main

## Build & Run Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run the app (requires Windows for full functionality)
python main.py

# Build the .exe
python -m PyInstaller build.spec
# Output: dist/ClaudeCodeInstaller.exe
```

No test suite or linter is currently configured.

## Architecture

**Entry point:** `main.py` — handles UAC elevation on Windows, then launches the CustomTkinter GUI.

**Thread model:** All installation work runs in a background daemon thread via `InstallerWorker`. Communication with the UI thread goes exclusively through a `queue.Queue`, polled every 100ms via `self.after()`. Never call widget methods from the worker thread.

**Module responsibilities:**

| Module | Purpose |
|--------|---------|
| `app/constants.py` | All magic values: registry keys, download URLs, shell commands, step indices. Single source of truth for configurable values. |
| `app/checks.py` | Pure dependency detection functions. Each returns `(bool, str)`. No side effects. |
| `app/releases.py` | Resolves latest Node.js LTS and Git release versions from remote APIs (npmmirror, nodejs.org, GitHub). Best-effort with fallbacks. |
| `app/downloader.py` | Streaming HTTP downloads with progress callbacks and mirror URL fallback chain. |
| `app/registry.py` | All `winreg` operations: context menu entries, system PATH management, Windows 11 classic menu toggle, Explorer restart. |
| `app/installer.py` | `InstallerWorker` orchestrates the 5-step install/repair/uninstall pipeline. Defines step constants `STEP_NODE=1` through `STEP_MENU=5`. |
| `app/window.py` | CustomTkinter GUI. `InstallerWindow` is the main window. `UninstallDialog` is a modal component-selection dialog. |

**5 installation steps (in order):**
1. Node.js — download MSI, install via `msiexec`
2. Git — download exe, silent install via InnoSetup flags
3. Claude Code — `npm install -g @anthropic-ai/claude-code` with CN mirror fallback
4. PATH — add Node.js and npm global bin to system PATH via registry
5. Context menu — write registry keys for "Open Claude Code here" shell entry

**Download mirror strategy:** Primary mirrors are China-optimized (npmmirror, Tsinghua TUNA). Falls back to official sources. System dependency versions (Node.js, Git) are resolved dynamically via `releases.py`; hardcoded versions in `constants.py` serve as offline fallbacks only.

## Key Conventions

- `winreg` imports are guarded with try/except so modules can be imported on non-Windows (e.g., macOS dev) without crashing.
- The exe is built as a single-file PyInstaller bundle with `uac_admin=True` in the manifest — no separate UAC prompt code needed at runtime beyond `main.py`'s fallback.
- Assets (`app/assets/icon.ico`) are bundled into the exe via PyInstaller `datas` and extracted at runtime via `sys._MEIPASS`.
- UI text is in Chinese (Simplified) for context menu labels and footer.
