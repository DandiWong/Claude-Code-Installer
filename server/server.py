"""
Lightweight server — E2E encrypted config + usage reporting + dashboard.

- GET /            → usage dashboard
- GET /api/stats   → aggregated usage stats (JSON)
- GET /info.json   → encrypted app info (version, min_version, notice, features)
- GET /config.json → encrypted config (providers, claude_settings)
- POST /report     → append usage event to log
"""

from __future__ import annotations

import json
import pathlib
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from urllib.parse import urlparse, parse_qs

from crypto import encrypt_and_sign

DATA_DIR = pathlib.Path(__file__).parent / "data"
APP_CONFIG_FILE = DATA_DIR / "app_config.json"
USAGE_LOG = DATA_DIR / "usage.log"
DASHBOARD_FILE = pathlib.Path(__file__).parent / "dashboard.html"


def _load_env() -> dict[str, str]:
    env_path = pathlib.Path(__file__).parent / ".env"
    result: dict[str, str] = {}
    if not env_path.is_file():
        return result
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                result[k.strip()] = v.strip()
    return result


CONFIG = _load_env()
HOST = CONFIG.get("HOST", "0.0.0.0")
PORT = int(CONFIG.get("PORT", "8080"))


def _read_usage_log(from_str: str = "", to_str: str = "") -> list[dict]:
    """Read usage.log and filter by date range (inclusive, UTC)."""
    if not USAGE_LOG.is_file():
        return []

    from_dt = from_str + "T00:00:00" if from_str else ""
    to_dt = to_str + "T23:59:59" if to_str else ""

    entries: list[dict] = []
    with open(USAGE_LOG, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = entry.get("timestamp", "")
            if from_dt and ts < from_dt:
                continue
            if to_dt and ts > to_dt:
                continue
            entries.append(entry)
    return entries


class ProvidersHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path

        if path in ("/", "/dashboard"):
            self._serve_dashboard()
        elif path == "/api/stats":
            qs = parse_qs(parsed.query)
            self._serve_stats(qs)
        elif path == "/info.json":
            self._serve_info()
        elif path == "/config.json":
            self._serve_config()
        else:
            self.send_error(404)

    def _serve_dashboard(self) -> None:
        if not DASHBOARD_FILE.is_file():
            self.send_error(404, "dashboard.html not found")
            return
        body = DASHBOARD_FILE.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _serve_stats(self, qs: dict[str, list[str]]) -> None:
        from_val = qs.get("from", [""])[0]
        to_val = qs.get("to", [""])[0]

        entries = _read_usage_log(from_val, to_val)

        startup_count = sum(1 for e in entries if e.get("event") == "startup")
        success_count = sum(1 for e in entries if e.get("event") == "success")

        provider_counts: dict[str, int] = {}
        for e in entries:
            if e.get("event") == "success":
                p = e.get("provider") or "未知"
                provider_counts[p] = provider_counts.get(p, 0) + 1

        startups = [e for e in entries if e.get("event") == "startup"]
        successes = [e for e in entries if e.get("event") == "success"]
        mixed = sorted(
            startups[-100:] + successes[-100:],
            key=lambda e: e.get("timestamp", ""),
        )

        body = json.dumps({
            "startup_count": startup_count,
            "success_count": success_count,
            "providers": provider_counts,
            "entries": mixed,
        }, ensure_ascii=False).encode("utf-8")

        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _load_app_config(self) -> dict:
        """Load app_config.json. Returns empty dict if missing or invalid."""
        if not APP_CONFIG_FILE.is_file():
            return {}
        try:
            return json.loads(APP_CONFIG_FILE.read_bytes())
        except Exception:
            return {}

    def _encrypt_and_respond(self, data: dict) -> None:
        """JSON-encode, encrypt, sign, and write the response."""
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        encrypted = encrypt_and_sign(payload)
        body = json.dumps(encrypted).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _serve_info(self) -> None:
        """Serve encrypted info section from app_config.json."""
        try:
            cfg = self._load_app_config()
            self._encrypt_and_respond(cfg.get("info", {}))
        except Exception as exc:
            self.send_error(500, str(exc))

    def _serve_config(self) -> None:
        """Serve encrypted providers + claude_settings from app_config.json."""
        try:
            cfg = self._load_app_config()
            payload = {
                "providers":       cfg.get("providers", []),
                "claude_settings": cfg.get("claude_settings", {}),
            }
            self._encrypt_and_respond(payload)
        except Exception as exc:
            self.send_error(500, str(exc))

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/report":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b""
            data = json.loads(raw) if raw else {}
        except (ValueError, json.JSONDecodeError):
            self.send_error(400, "Invalid JSON")
            return

        event = data.get("event", "").strip()
        if event not in ("startup", "success"):
            self.send_error(400, "Invalid event type")
            return

        ip = (
            self.headers.get("X-Real-IP")
            or self.headers.get("X-Forwarded-For", "").split(",")[0].strip()
            or self.client_address[0]
        )
        entry = {
            "event": event,
            "timestamp": data.get("timestamp") or datetime.now(timezone.utc).isoformat(),
            "provider": data.get("provider", ""),
            "ip": ip,
        }
        with open(USAGE_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

        self.send_response(204)
        self.end_headers()

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"[{self.log_date_time_string()}] {fmt % args}")


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    server = ThreadedHTTPServer((HOST, PORT), ProvidersHandler)
    print(f"Config API running on http://{HOST}:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.server_close()


if __name__ == "__main__":
    main()
