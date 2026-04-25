"""Build helpers invoked by build.bat / deploy.sh. Never run directly."""

import hashlib
import json
import pathlib
import re
import sys
import zipfile

# Project root (one level up from scripts/)
ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = pathlib.Path(__file__).resolve().parent

CONFIG_PATH = SCRIPTS / "config.json"


def _load_config() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def _save_config(cfg: dict) -> None:
    CONFIG_PATH.write_text(
        json.dumps(cfg, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def current_version() -> str:
    """Read APP_VERSION from constants.py."""
    content = (ROOT / "app/constants.py").read_text(encoding="utf-8")
    m = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', content)
    return m.group(1) if m else "?.?"


def set_version(new_ver: str) -> None:
    """Update APP_VERSION in constants.py AND config.json info.latest.version."""
    # constants.py
    path = ROOT / "app/constants.py"
    content = path.read_text(encoding="utf-8")
    content = re.sub(
        r'APP_VERSION\s*=\s*"[^"]+"',
        f'APP_VERSION = "{new_ver}"',
        content,
    )
    path.write_text(content, encoding="utf-8")
    print(f"  APP_VERSION -> {new_ver}")

    # config.json (optional — gitignored, may not exist in fresh clones)
    if CONFIG_PATH.is_file():
        cfg = _load_config()
        cfg.setdefault("info", {}).setdefault("latest", {})
        cfg["info"]["latest"]["version"] = new_ver
        _save_config(cfg)
        print(f"  config.json info.latest.version -> {new_ver}")
    else:
        print("  config.json not found, skipping (copy from config.example.json before deploying)")


def package(new_ver: str) -> None:
    """Zip the exe, compute SHA-256, update config.json, archive to release/.

    Exits with code 2 if exe unchanged since last release.
    """
    exe = SCRIPTS / "dist/ClaudeCodeInstaller.exe"
    if not exe.is_file():
        sys.exit("  ERROR: dist/ClaudeCodeInstaller.exe not found")

    exe_sha256 = hashlib.sha256(exe.read_bytes()).hexdigest()

    # Skip if exe unchanged (requires config.json — gitignored in CI)
    if CONFIG_PATH.is_file():
        cfg = _load_config()
        if cfg.get("info", {}).get("latest", {}).get("exe_sha256") == exe_sha256:
            print("UNCHANGED")
            return
    else:
        cfg = {}

    # Create zip in www/assets/release/
    out_dir = ROOT / "www/assets/release"
    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / "ClaudeCodeInstaller.zip"

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(exe, exe.name)

    zip_sha256 = hashlib.sha256(zip_path.read_bytes()).hexdigest()

    # Update config.json (only if it exists)
    if CONFIG_PATH.is_file():
        cfg.setdefault("info", {}).setdefault("latest", {})
        cfg["info"]["latest"]["version"] = new_ver
        cfg["info"]["latest"]["sha256"] = zip_sha256
        cfg["info"]["latest"]["exe_sha256"] = exe_sha256
        _save_config(cfg)
        print(f"  config.json updated.")

    # Archive to release/vX.X/
    release_dir = ROOT / f"release/v{new_ver}"
    release_dir.mkdir(parents=True, exist_ok=True)
    archive_path = release_dir / "ClaudeCodeInstaller.zip"
    archive_path.write_bytes(zip_path.read_bytes())

    kb = zip_path.stat().st_size // 1024
    print(f"  Zip:     {zip_path}  ({kb} KB)")
    print(f"  SHA256:  {zip_sha256}")
    print(f"  Archive: {archive_path}")
    print(f"  config.json updated.")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""

    if cmd == "current-version":
        print(current_version())
    elif cmd == "set-version" and len(sys.argv) > 2:
        set_version(sys.argv[2])
    elif cmd == "package" and len(sys.argv) > 2:
        package(sys.argv[2])
    else:
        print("Usage: _build_helpers.py {current-version|set-version|package} [VERSION]")
        sys.exit(1)
