"""
Wizard-style installer UI — step-by-step with progress bars.

Step flow:
  Welcome  →  (1/6)…(5/6) install with progress bar  →  (6/6) model config  →  Complete

Thread-safety rule: NEVER call widget methods from the worker thread.
All cross-thread communication goes through self._q (queue.Queue),
drained every 100 ms via self.after(100, self._poll_queue).
"""

from __future__ import annotations

import json
import os
import queue
import sys
import threading

import customtkinter as ctk
from PIL import Image

from app.installer import (
    InstallerWorker,
    STEP_NODE, STEP_GIT, STEP_CLAUDE, STEP_PATH, STEP_MENU, STEP_MODEL,
)
from app.constants import APP_NAME, APP_VERSION
from app import remote_config
from app.updater import download_update, apply_update
from app.model_config import test_connection, check_model_config
from app.config_manager import save_model_config, get_model_config
from app.usage_reporter import report_startup, report_success
from app.checks import (
    check_node, check_git, check_claude_code,
    check_path_entries, check_context_menu,
)

# ── Visual constants ──────────────────────────────────────────────

WIN_W, WIN_H = 640, 500
BANNER_H = 65

DARK_BG    = "#2c2c32"
PANEL_BG   = "#35343e"
BANNER_BG  = PANEL_BG
ACCENT     = "#da7756"
SUCCESS    = "#4ade80"
ERROR_COL  = "#f87171"
WARN_COL   = "#fbbf24"
TEXT_MUTED = "#8b8a8e"
TEXT_LIGHT = "#e4e4e7"

FONT_BANNER_TITLE = ("Segoe UI", 18, "bold")
FONT_BANNER_SUB   = ("Segoe UI", 12)
FONT_PAGE_TITLE   = ("Segoe UI", 16, "bold")
FONT_BODY         = ("Segoe UI", 13)
FONT_LABEL        = ("Segoe UI", 13, "bold")
FONT_STEP_DETAIL  = ("Segoe UI", 12)
FONT_BTN          = ("Segoe UI", 13)
FONT_BTN_BOLD     = ("Segoe UI", 14, "bold")
FONT_STEP_ICON    = ("Segoe UI", 15)

# Page indices
PAGE_WELCOME  = 0
PAGE_INSTALL  = 1
PAGE_COMPLETE = 3
PAGE_UNINSTALL = 4
PAGE_MODEL_SELECT = 5
PAGE_PROVIDER_CONFIG = 6
PAGE_CUSTOM_PROVIDER = 7
PAGE_UPDATE = 8

# Map worker step-id → display info
STEP_TITLES = {
    STEP_NODE:   "(1/6) 安装 Node.js",
    STEP_GIT:    "(2/6) 安装 Git",
    STEP_CLAUDE: "(3/6) 安装 Claude Code",
    STEP_PATH:   "(4/6) 配置 PATH",
    STEP_MENU:   "(5/6) 安装右键菜单",
}

UNINSTALL_TITLES = {
    STEP_NODE:   "卸载 Node.js",
    STEP_GIT:    "卸载 Git",
    STEP_CLAUDE: "卸载 Claude Code",
    STEP_PATH:   "移除 PATH",
    STEP_MENU:   "移除右键菜单",
}

STEP_NAMES = {
    STEP_NODE:   "Node.js",
    STEP_GIT:    "Git",
    STEP_CLAUDE: "Claude Code",
    STEP_PATH:   "PATH",
    STEP_MENU:   "右键菜单",
}

STEP_ORDER = [STEP_NODE, STEP_GIT, STEP_CLAUDE, STEP_PATH, STEP_MENU]


def _load_providers() -> tuple[list[dict], str]:
    """Return provider presets from remote_config cache.

    Returns (providers, error_msg). On success error_msg is empty.
    """
    providers = remote_config.get_providers()
    if providers:
        return providers, ""
    return [], "无法获取服务器配置（请检查网络连接）"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Wizard Shell
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class InstallerWindow(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")

        self.title(f"{APP_NAME} v{APP_VERSION}")
        self.geometry(f"{WIN_W}x{WIN_H}")
        self.resizable(False, False)
        self.configure(fg_color=DARK_BG)

        self._try_set_icon()
        self._set_title_bar_color()

        self._q: queue.Queue = queue.Queue()
        self._worker = InstallerWorker(self._q)
        self._current_page = -1

        # Layout: banner + content (no footer — buttons live inside pages)
        self._build_banner()
        self._content = ctk.CTkFrame(self, fg_color=DARK_BG)
        self._content.pack(fill="both", expand=True)

        # Pages
        self._pages: dict[int, ctk.CTkFrame] = {
            PAGE_WELCOME:   WelcomePage(self._content, self),
            PAGE_INSTALL:   InstallPage(self._content, self),
            PAGE_COMPLETE:  CompletePage(self._content, self),
            PAGE_UNINSTALL: UninstallPage(self._content, self),
            PAGE_MODEL_SELECT: ModelSelectPage(self._content, self),
            PAGE_PROVIDER_CONFIG: ProviderConfigPage(self._content, self),
            PAGE_CUSTOM_PROVIDER: CustomProviderPage(self._content, self),
            PAGE_UPDATE: UpdatePage(self._content, self),
        }
        self._show_page(PAGE_WELCOME)
        self.after(100, self._poll_queue)
        report_startup()
        remote_config.fetch_info(self._on_config_ready)

    def _on_config_ready(self, latest: dict | None) -> None:
        import logging; _log = logging.getLogger("installer").debug
        _log(f"_on_config_ready: latest={latest}")
        if latest:
            forced = bool(latest.pop("_forced", False))
            self.after(0, lambda: self._pages[PAGE_WELCOME].show_update_banner(latest, forced=forced))  # type: ignore
        # Show notice banner regardless of update availability
        self.after(0, self._check_notice)

    def _check_notice(self) -> None:
        text, level = remote_config.get_notice()
        if text:
            self._pages[PAGE_WELCOME].show_notice_banner(text, level)  # type: ignore

    # ── helpers ──

    def _try_set_icon(self) -> None:
        if sys.platform != "win32":
            return
        try:
            if getattr(sys, "frozen", False):
                base = sys._MEIPASS  # type: ignore[attr-defined]
            else:
                base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            icon = os.path.join(base, "assets", "icon.ico")
            if os.path.isfile(icon):
                self.iconbitmap(icon)
        except Exception:
            pass

    def _set_title_bar_color(self) -> None:
        if sys.platform != "win32":
            return
        try:
            import ctypes
            from ctypes import windll, byref, sizeof

            hwnd = ctypes.windll.user32.GetParent(self.winfo_id())  # type: ignore[attr-defined]

            # Windows 10+: dark title bar (DWMWA_USE_IMMERSIVE_DARK_MODE = 20)
            windll.dwmapi.DwmSetWindowAttribute(
                hwnd, 20, byref(ctypes.c_int(1)), sizeof(ctypes.c_int),
            )

            # Windows 11+: custom caption color (DWMWA_CAPTION_COLOR = 35)
            # COLORREF is BGR: #35343e → 0x003e3435
            color = ctypes.c_int(0x003E3435)
            windll.dwmapi.DwmSetWindowAttribute(
                hwnd, 35, byref(color), sizeof(ctypes.c_int),
            )
        except Exception:
            pass

    def _build_banner(self) -> None:
        banner = ctk.CTkFrame(
            self, fg_color=BANNER_BG, corner_radius=0, height=BANNER_H,
        )
        banner.pack(fill="x")
        banner.pack_propagate(False)

        # Banner image
        try:
            if getattr(sys, "frozen", False):
                base = sys._MEIPASS  # type: ignore[attr-defined]
            else:
                base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            banner_path = os.path.join(base, "assets", "banner.png")
            _banner_img = ctk.CTkImage(
                light_image=Image.open(banner_path),
                dark_image=Image.open(banner_path),
                size=(WIN_W, BANNER_H),
            )
            ctk.CTkLabel(banner, image=_banner_img, text="").place(x=0, y=0)
            self._banner_img = _banner_img  # prevent GC
        except Exception:
            # Fallback to text if image not found
            ctk.CTkLabel(
                banner, text=APP_NAME,
                font=FONT_BANNER_TITLE, text_color="white",
            ).place(x=20, y=14)

        ctk.CTkLabel(
            banner, text="Copyright \u00a9 Dandi\u4e5d\u7237",
            font=FONT_BANNER_TITLE, text_color="#8b8a8e",
        ).place(relx=1.0, x=-20, y=20, anchor="ne")

    def _show_page(self, index: int) -> None:
        for page in self._pages.values():
            page.pack_forget()
        self._pages[index].pack(fill="both", expand=True)
        self._current_page = index
        on_show = getattr(self._pages[index], "_on_show", None)
        if on_show:
            on_show()

    # ── queue polling (UI thread) ──

    def _poll_queue(self) -> None:
        try:
            while True:
                msg = self._q.get_nowait()
                mtype = msg.get("type")

                # Always consume 'done' to avoid losing terminal messages
                if mtype == "done" and self._current_page == PAGE_INSTALL:
                    page: InstallPage = self._pages[PAGE_INSTALL]  # type: ignore
                    page.on_install_done(msg.get("success", False))

                elif mtype == "test_result" and self._current_page == PAGE_PROVIDER_CONFIG:
                    self._pages[PAGE_PROVIDER_CONFIG].show_test_result(  # type: ignore
                        msg.get("ok", False), msg.get("msg", ""),
                    )

                elif mtype == "custom_test_result" and self._current_page == PAGE_CUSTOM_PROVIDER:
                    self._pages[PAGE_CUSTOM_PROVIDER].show_test_result(  # type: ignore
                        msg.get("ok", False), msg.get("msg", ""),
                    )

                elif self._current_page != PAGE_INSTALL:
                    continue

                elif mtype == "step":
                    self._pages[PAGE_INSTALL].set_step_status(  # type: ignore
                        msg["step"], msg["status"],
                    )

                elif mtype == "detail":
                    self._pages[PAGE_INSTALL].set_step_detail(  # type: ignore
                        msg["step"], msg["text"],
                    )

                elif mtype == "progress":
                    d, t = msg.get("downloaded", 0), msg.get("total", 0)
                    if t > 0:
                        self._pages[PAGE_INSTALL].set_download_progress(  # type: ignore
                            msg["step"], int(d * 100 / t),
                        )

        except queue.Empty:
            pass
        except Exception:
            # Prevent queue poll from dying on any unexpected error
            pass

        self.after(100, self._poll_queue)

    def _set_main_buttons_state(self, state: str) -> None:
        """Compatibility shim."""
        pass


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Welcome Page
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class WelcomePage(ctk.CTkFrame):
    # (step_key, display_label)
    CHECK_ITEMS = [
        ("node",   "Node.js"),
        ("git",    "Git"),
        ("claude", "Claude Code"),
        ("path",   "系统环境变量"),
        ("menu",   "右键菜单"),
        ("model",  "模型配置"),
    ]

    def __init__(self, parent: ctk.CTkFrame, wizard: InstallerWindow) -> None:
        super().__init__(parent, fg_color=PANEL_BG)
        self._wizard = wizard
        self._check_widgets: dict[str, dict] = {}
        self._check_results: dict[str, bool] = {}

        ctk.CTkLabel(
            self, text="欢迎使用 Claude Code 懒人免翻墙一键安装包",
            font=FONT_PAGE_TITLE, text_color="white",
        ).place(relx=0.5, y=24, anchor="n")

        # Update banner (hidden by default)
        self._update_banner = ctk.CTkFrame(
            self, fg_color="#c45f00", corner_radius=6, height=34,
        )
        self._update_label = ctk.CTkLabel(
            self._update_banner, text="",
            font=FONT_STEP_DETAIL, text_color="white", anchor="w",
        )
        self._update_label.place(x=10, rely=0.5, anchor="w")
        self._update_btn = ctk.CTkButton(
            self._update_banner, text="立即更新",
            width=80, height=24, font=FONT_BTN,
            fg_color="#ff8c00", hover_color="#e07800", text_color="white",
            command=self._go_to_update,
        )
        self._update_btn.place(relx=1.0, x=-10, rely=0.5, anchor="e")
        self._update_info: dict | None = None

        # Notice banner (hidden by default)
        self._notice_banner = ctk.CTkFrame(
            self, fg_color="#2a5a8a", corner_radius=6, height=34,
        )
        self._notice_label = ctk.CTkLabel(
            self._notice_banner, text="",
            font=FONT_STEP_DETAIL, text_color="white", anchor="w",
        )
        self._notice_label.place(x=10, rely=0.5, anchor="w")

        # Status rows for each component
        self._check_container = ctk.CTkFrame(self, fg_color="transparent", width=400)
        self._check_container_y = 66
        self._banners_shown = 0  # tracks how many banners are visible
        self._check_container.place(relx=0.5, x=80, y=self._check_container_y, anchor="n")
        for i, (key, label) in enumerate(self.CHECK_ITEMS):
            row = ctk.CTkFrame(self._check_container, fg_color="transparent")

            icon = ctk.CTkLabel(
                row, text="\u25cb",
                font=FONT_STEP_ICON, text_color=TEXT_MUTED, width=22,
            )
            icon.pack(side="left")

            ctk.CTkLabel(
                row, text=label,
                font=FONT_LABEL, text_color="white",
                width=140, anchor="w",
            ).pack(side="left", padx=(4, 0))

            detail = ctk.CTkLabel(
                row, text="检测中...",
                font=FONT_STEP_DETAIL, text_color=TEXT_MUTED, anchor="w", width=200,
            )
            detail.pack(side="left", padx=(4, 0))

            row.pack(anchor="w", pady=1)
            self._check_widgets[key] = {"icon": icon, "detail": detail}

        # Buttons
        self._btn_start = ctk.CTkButton(
            self, text="\u5f00\u59cb\u5b89\u88c5",
            width=100, height=40,
            font=FONT_BTN_BOLD, fg_color=ACCENT, hover_color="#c4694a",
            text_color="white",
            command=self._start,
        )
        self._btn_start.place(relx=0.25, rely=0.85, anchor="center")

        self._btn_repair = ctk.CTkButton(
            self, text="修复", width=100, height=40,
            font=FONT_BTN_BOLD, fg_color=WARN_COL, hover_color="#e0a820",
            text_color="white",
            command=self._repair,
        )
        self._btn_repair.place(relx=0.5, rely=0.85, anchor="center")

        self._btn_uninstall = ctk.CTkButton(
            self, text="卸载", width=100, height=40,
            font=FONT_BTN_BOLD, fg_color="#4a1d1d", hover_color="#5c2727",
            text_color="white",
            command=self._open_uninstall,
        )
        self._btn_uninstall.place(relx=0.75, rely=0.85, anchor="center")

        # Run checks in background
        self.after(300, self._run_checks)

    def _run_checks(self) -> None:
        threading.Thread(target=self._do_checks, daemon=True).start()

    def _do_checks(self) -> None:
        checks = [
            ("node",   lambda: check_node()),
            ("git",    lambda: check_git()),
            ("claude", lambda: check_claude_code()),
            ("path",   self._check_path),
            ("menu",   self._check_menu),
            ("model",  lambda: check_model_config()),
        ]
        for key, fn in checks:
            result = fn()
            self.after(0, lambda k=key, r=result: self._update_check(k, r))

    def _check_path(self) -> tuple[bool, str]:
        missing = check_path_entries()
        if not missing:
            return True, "已配置"
        return False, f"缺少 {len(missing)} 个路径"

    def _check_menu(self) -> tuple[bool, str]:
        ok = check_context_menu()
        return ok, "已安装" if ok else "未安装"

    def _update_check(self, key: str, result: tuple[bool, str]) -> None:
        w = self._check_widgets.get(key)
        if not w:
            return
        ok, detail = result
        self._check_results[key] = ok
        if ok:
            w["icon"].configure(text="\u2713", text_color=SUCCESS)
            w["detail"].configure(text=detail, text_color=SUCCESS)
        else:
            w["icon"].configure(text="\u2717", text_color=ERROR_COL)
            w["detail"].configure(text=detail, text_color=TEXT_MUTED)

        if len(self._check_results) == len(self.CHECK_ITEMS):
            all_ok = all(self._check_results.values())
            any_installed = any(self._check_results.values())
            if getattr(self, "_force_update", False):
                # Minimum version not met — lock all action buttons
                for btn_name in ("_btn_repair", "_btn_uninstall"):
                    if hasattr(self, btn_name):
                        getattr(self, btn_name).configure(state="disabled")
            else:
                self._btn_uninstall.configure(
                    state="normal" if any_installed else "disabled",
                )
                self._btn_repair.configure(
                    state="disabled" if all_ok or not any_installed else "normal",
                )

    def _next_banner_y(self) -> int:
        """Return y position for the next banner and advance the counter."""
        y = 56 + self._banners_shown * 40
        self._banners_shown += 1
        self._check_container_y = 66 + self._banners_shown * 40
        self._check_container.place(relx=0.5, x=80, y=self._check_container_y, anchor="n")
        return y

    def show_update_banner(self, info: dict, forced: bool = False) -> None:
        """Show the update banner. Called from UI thread."""
        import logging; _log = logging.getLogger("installer").debug
        import traceback
        try:
            self._update_info = info
            version = info.get("version", "")
            notes = info.get("release_notes", "")
            _log(f"show_update_banner: version={version}, forced={forced}")

            if forced:
                text = f"当前版本过旧（最低要求 v{version}），必须更新后才能使用"
                for btn in ("_btn_start", "_btn_repair", "_btn_uninstall"):
                    if hasattr(self, btn):
                        getattr(self, btn).configure(state="disabled")
                self._force_update = True
            else:
                text = f"新版本 v{version} 可用：{notes}" if notes else f"新版本 v{version} 可用"
                self._force_update = False

            _log("step1: setting text")
            self._update_label.configure(text=text)
            _log("step2: _next_banner_y")
            y = self._next_banner_y()
            _log(f"step3: place at y={y}")
            self._update_banner.place(relx=0.05, y=y, relwidth=0.9)
            _log("step4: configure colors")
            if forced:
                self._update_banner.configure(fg_color="#8b0000")
                self._update_btn.configure(fg_color="#cc0000", hover_color="#aa0000", text="立即更新")
            else:
                self._update_banner.configure(fg_color="#c45f00")
                self._update_btn.configure(fg_color="#ff8c00", hover_color="#e07800")
            _log("step5: lift")
            self._update_banner.lift()
            self._update_label.lift()
            self._update_btn.lift()
            _log("banner done")
        except Exception as e:
            _log(f"show_update_banner EXCEPTION: {e}\n{traceback.format_exc()}")

    def show_notice_banner(self, text: str, level: str = "info") -> None:
        """Show a server-side notice banner below any update banner. Called from UI thread."""
        level_colors = {
            "info":    "#2a5a8a",
            "warning": "#7a5a00",
            "error":   "#7a1a1a",
        }
        self._notice_banner.configure(fg_color=level_colors.get(level, "#2a5a8a"))
        self._notice_label.configure(text=text)
        y = self._next_banner_y()
        self._notice_banner.place(relx=0.05, y=y, relwidth=0.9)
        self._notice_banner.lift()
        self._notice_label.lift()

    def _go_to_update(self) -> None:
        if self._update_info:
            self._wizard._pages[PAGE_UPDATE].start_download(self._update_info)  # type: ignore
        self._wizard._show_page(PAGE_UPDATE)

    def _start(self) -> None:
        self._wizard._show_page(PAGE_INSTALL)
        self._wizard._pages[PAGE_INSTALL].start_installation(  # type: ignore
            self._wizard._worker,
        )

    def _repair(self) -> None:
        non_model_ok = all(
            v for k, v in self._check_results.items() if k != "model"
        )
        if non_model_ok and not self._check_results.get("model", False):
            self._wizard._show_page(PAGE_MODEL_SELECT)
            return
        self._wizard._show_page(PAGE_INSTALL)
        self._wizard._pages[PAGE_INSTALL].start_repair(  # type: ignore
            self._wizard._worker,
        )

    def _open_uninstall(self) -> None:
        self._wizard._show_page(PAGE_UNINSTALL)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Install Page — (1/6) … (5/6) with progress bar per step
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class InstallPage(ctk.CTkFrame):
    def __init__(self, parent: ctk.CTkFrame, wizard: InstallerWindow) -> None:
        super().__init__(parent, fg_color=PANEL_BG)
        self._wizard = wizard
        self.is_complete = False
        self._is_uninstall = False
        self._step_counter = 0
        self._uninstall_total = 6

        # --- current-step header ---
        self._title = ctk.CTkLabel(
            self, text="", font=FONT_PAGE_TITLE, text_color="white",
        )
        self._title.place(relx=0.5, y=20, anchor="n")

        self._progress = ctk.CTkProgressBar(self, width=560, height=10, progress_color=ACCENT)
        self._progress.place(relx=0.5, y=56, anchor="n")
        self._progress.set(0)

        self._status = ctk.CTkLabel(
            self, text="", font=FONT_BODY, text_color=TEXT_MUTED,
        )
        self._status.place(relx=0.5, y=76, anchor="n")

        # --- completed-steps summary (pre-created, initially hidden) ---
        self._summary_container = ctk.CTkFrame(self, fg_color="transparent", width=400)
        self._summary_container.place(relx=0.5, x=80, y=110, anchor="n")
        self._summary_rows: dict[int, dict] = {}
        for i, step_id in enumerate(STEP_ORDER):
            row = ctk.CTkFrame(self._summary_container, fg_color="transparent")

            icon = ctk.CTkLabel(
                row, text="\u2713", font=FONT_STEP_ICON,
                text_color=SUCCESS, width=22,
            )
            icon.pack(side="left")

            ctk.CTkLabel(
                row, text=STEP_NAMES[step_id],
                font=FONT_LABEL, text_color="white",
                width=140, anchor="w",
            ).pack(side="left", padx=(4, 0))

            detail = ctk.CTkLabel(
                row, text="", font=FONT_STEP_DETAIL,
                text_color=TEXT_MUTED, anchor="w", width=200,
            )
            detail.pack(side="left", padx=(4, 0))

            self._summary_rows[step_id] = {
                "frame": row, "detail": detail, "icon": icon,
                "y": i * 32, "placed": False,
            }

        # --- Next button (hidden until all install steps finish) ---
        self._btn_next = ctk.CTkButton(
            self, text="\u4e0b\u4e00\u6b65",
            width=140, height=38,
            font=FONT_BTN_BOLD, fg_color=ACCENT, hover_color="#c4694a",
            text_color="white",
            command=self._go_next,
        )

    # ── public API (called from wizard queue poll) ──

    def start_installation(self, worker: InstallerWorker) -> None:
        self._reset()
        self._is_uninstall = False
        self._step_counter = 0
        worker.start_full_install()

    def start_repair(self, worker: InstallerWorker) -> None:
        self._reset()
        self._is_uninstall = False
        self._step_counter = 0
        self._title.configure(text="修复中...")
        worker.start_repair()

    def start_uninstall(self, worker: InstallerWorker, selection: set[int]) -> None:
        self._reset()
        self._is_uninstall = True
        self._step_counter = 0
        self._uninstall_total = len(selection)
        self._title.configure(text="卸载中...")
        worker.start_uninstall(selection)

    def set_step_status(self, step: int, status: str) -> None:
        if status == "running":
            self._step_counter += 1
            step_names = UNINSTALL_TITLES if self._is_uninstall else STEP_TITLES
            name = step_names.get(step, STEP_NAMES.get(step, ""))
            if not name:
                self._status.configure(text="处理中...")
                return
            if self._is_uninstall:
                total = self._uninstall_total
                title = f"({self._step_counter}/{total}) {name}"
            else:
                title = name
            self._title.configure(text=title)
            # Show fixed half-bar for steps without real download progress
            if step == STEP_CLAUDE or self._is_uninstall:
                self._progress.set(0.5)
            else:
                self._progress.set(0)
            self._status.configure(text="执行中...")

        elif status == "ok":
            self._show_summary_row(step, ok=True)
            self._progress.set(1.0)
            self._status.configure(text="")

        elif status == "error":
            self._show_summary_row(step, ok=False)
            self._progress.set(0)
            self._status.configure(text="")

        elif status == "pending":
            # Used by uninstall to mark a removed component
            row = self._summary_rows.get(step)
            if row:
                row["icon"].configure(text="\u2713", text_color=SUCCESS)
                row["detail"].configure(text="已移除")
                if not row["placed"]:
                    row["frame"].place(x=0, y=row["y"], anchor="nw")
                    row["placed"] = True
            elif step == STEP_MODEL:
                self._status.configure(text="模型配置已移除")

    def set_step_detail(self, step: int, text: str) -> None:
        self._status.configure(text=text)
        row = self._summary_rows.get(step)
        if not row:
            return
        # Cache detail text even if row not yet placed
        row["pending_detail"] = text
        if row["placed"]:
            row["detail"].configure(text=text)

    def set_download_progress(self, step: int, pct: int) -> None:
        self._progress.set(pct / 100.0)
        self._status.configure(text=f"下载中... {pct}%")

    def on_install_done(self, success: bool) -> None:
        self.is_complete = True
        if self._is_uninstall:
            self._title.configure(
                text="卸载完成" if success
                else "卸载完成（部分失败）",
            )
            self._btn_next.configure(text="\u5b8c\u6210")
        else:
            self._title.configure(
                text="安装完成" if success
                else "安装完成（部分失败）",
            )
            self._btn_next.configure(text="\u4e0b\u4e00\u6b65")
        self._progress.place_forget()
        self._status.configure(text="")
        self._btn_next.place(relx=0.5, rely=0.85, anchor="center")

    # ── internal ──

    def _show_summary_row(self, step: int, ok: bool) -> None:
        row = self._summary_rows.get(step)
        if not row:
            return
        if ok:
            row["icon"].configure(text="\u2713", text_color=SUCCESS)
        else:
            row["icon"].configure(text="\u2717", text_color=ERROR_COL)
        if not row["placed"]:
            row["frame"].place(x=0, y=row["y"], anchor="nw")
            row["placed"] = True
            cached = row.get("pending_detail", "")
            if cached:
                row["detail"].configure(text=cached)

    def _go_next(self) -> None:
        if self._is_uninstall:
            self._wizard.destroy()
        else:
            self._wizard._show_page(PAGE_MODEL_SELECT)

    def _reset(self) -> None:
        self.is_complete = False
        self._step_counter = 0
        self._uninstall_total = 6
        self._title.configure(text="(1/6) 安装 Node.js")
        self._progress.set(0)
        self._progress.place(relx=0.5, y=56, anchor="n")
        self._status.configure(text="准备中...")
        for row in self._summary_rows.values():
            row["frame"].place_forget()
            row["detail"].configure(text="")
            row["icon"].configure(text="\u2713", text_color=SUCCESS)
            row["placed"] = False
            row["pending_detail"] = ""
        self._btn_next.place_forget()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Model Select Page — choose a provider
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

CARD_BG = "#3a3a44"
CARD_HOVER = "#44444f"
CARD_SELECTED = "#504040"
CARD_BORDER_SELECTED = "#da7756"


class ModelSelectPage(ctk.CTkFrame):
    CARD_Y_START = 95
    CARD_W = 480
    CARD_H = 72
    CARD_GAP = 12

    def __init__(self, parent: ctk.CTkFrame, wizard: InstallerWindow) -> None:
        super().__init__(parent, fg_color=PANEL_BG)
        self._wizard = wizard
        self._selected: str | None = None
        self._cards: dict[str, ctk.CTkFrame] = {}
        self._providers: list[dict] = []

        # Title
        ctk.CTkLabel(
            self, text="配置模型 API",
            font=FONT_PAGE_TITLE, text_color="white",
        ).place(relx=0.5, y=16, anchor="n")

        # Explanation
        ctk.CTkLabel(
            self,
            text="Claude Code 需要 API Key 才能使用。国内用户无法直接订阅 Claude 官方服务，\n"
                 "需要通过第三方中转站获取 API Key。请选择一个供应商：",
            font=FONT_BODY, text_color=TEXT_MUTED, justify="left",
        ).place(relx=0.5, y=46, anchor="n")

        # Card container (cards rebuilt on each show)
        self._card_container = ctk.CTkFrame(self, fg_color="transparent")
        self._card_container.place(relx=0.5, y=self.CARD_Y_START, anchor="n")

        # Error label + retry button (shown when remote fetch fails)
        self._error_label = ctk.CTkLabel(
            self, text="", font=FONT_BODY, text_color=ERROR_COL, wraplength=440,
        )
        self._error_label.place(relx=0.5, y=self.CARD_Y_START, anchor="n")

        self._retry_btn = ctk.CTkButton(
            self, text="重试", width=100, height=32,
            font=FONT_BTN, fg_color=ACCENT, hover_color="#c4694a",
            text_color="white", command=self._build_cards,
        )

        # 下一步 button
        self._btn_next = ctk.CTkButton(
            self, text="下一步",
            width=140, height=40,
            font=FONT_BTN_BOLD, fg_color=ACCENT, hover_color="#c4694a",
            text_color="white",
            state="disabled",
            command=self._next,
        )
        self._btn_next.place(relx=0.5, rely=0.85, anchor="center")

        # 自定义 link (放在按钮右侧)
        custom_label = ctk.CTkLabel(
            self, text="自定义",
            font=("Segoe UI", 13), text_color="#d4a574", cursor="hand2",
        )
        custom_label.place(x=320 + 80 + 12, rely=0.85, anchor="w")
        custom_label.bind("<Button-1>", lambda _e: self._wizard._show_page(PAGE_CUSTOM_PROVIDER))

    def _on_show(self) -> None:
        self._build_cards()

    def _build_cards(self) -> None:
        for card in self._cards.values():
            card.destroy()
        self._cards.clear()

        providers, error = _load_providers()
        self._providers = [p for p in providers if p.get("name") != "自定义"]
        self._selected = None
        self._btn_next.configure(state="disabled")

        if error:
            if remote_config.is_config_loading():
                # Phase 2 still in flight — show loading state and auto-retry
                self._error_label.configure(text="正在加载配置...", text_color=TEXT_MUTED)
                self._retry_btn.place_forget()
                self.after(500, self._build_cards)
                return
            self._error_label.configure(text=error, text_color=ERROR_COL)
            self._retry_btn.place(relx=0.5, y=self.CARD_Y_START + 30, anchor="n")
            return

        self._error_label.configure(text="")
        self._retry_btn.place_forget()

        for i, p in enumerate(self._providers):
            name = p["name"]
            advantage = p.get("advantage", "")
            card = ctk.CTkFrame(
                self._card_container, fg_color=CARD_BG, corner_radius=8,
                cursor="hand2", width=self.CARD_W, height=self.CARD_H,
            )
            card.pack_propagate(False)
            card.pack(anchor="w", pady=(0 if i == 0 else self.CARD_GAP, 0))

            ctk.CTkLabel(
                card, text=name,
                font=("Segoe UI", 14, "bold"), text_color="white",
            ).pack(anchor="w", padx=20, pady=(6, 2))

            ctk.CTkLabel(
                card, text=advantage,
                font=("Segoe UI", 13, "bold"), text_color="#d4a574",
            ).pack(anchor="w", padx=20)

            for widget in card.winfo_children():
                widget.bind("<Button-1>", lambda _e, n=name: self._select(n))
            card.bind("<Button-1>", lambda _e, n=name: self._select(n))
            self._cards[name] = card

    def _select(self, name: str) -> None:
        self._selected = name
        for n, card in self._cards.items():
            if n == name:
                card.configure(fg_color=CARD_SELECTED, border_color=CARD_BORDER_SELECTED, border_width=2)
            else:
                card.configure(fg_color=CARD_BG, border_width=0)
        self._btn_next.configure(state="normal")

    def _next(self) -> None:
        if not self._selected:
            return
        # Find selected provider data and open website
        provider_data = {}
        for p in self._providers:
            if p.get("name") == self._selected:
                provider_data = p
                url = p.get("website", "")
                if url:
                    import webbrowser
                    webbrowser.open(url)
                break
        # Navigate to provider config page
        config_page: ProviderConfigPage = self._wizard._pages[PAGE_PROVIDER_CONFIG]  # type: ignore
        config_page.set_provider(provider_data)
        self._wizard._show_page(PAGE_PROVIDER_CONFIG)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Provider Config Page — configure API key for selected provider
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class ProviderConfigPage(ctk.CTkFrame):
    _WIN_H_NORMAL = 500
    _WIN_H_EXPANDED = 700
    _TEST_Y_COLLAPSED = 292
    _TEST_Y_EXPANDED = 466
    _ADV_OPUS_Y = 292
    _ADV_SONNET_Y = 350
    _ADV_HAIKU_Y = 408

    def __init__(self, parent: ctk.CTkFrame, wizard: InstallerWindow) -> None:
        super().__init__(parent, fg_color=PANEL_BG)
        self._wizard = wizard
        self._provider_data: dict = {}
        self._advanced_visible = False

        # Title (updated dynamically)
        self._title = ctk.CTkLabel(
            self, text="",
            font=FONT_PAGE_TITLE, text_color="white",
        )
        self._title.place(relx=0.5, y=18, anchor="n")

        ctk.CTkLabel(
            self, text="请输入您从供应商获取的 API Key",
            font=FONT_BODY, text_color=TEXT_MUTED,
        ).place(relx=0.5, y=46, anchor="n")

        y = 80
        gap = 60
        x = 130

        # BASE_URL (read-only)
        ctk.CTkLabel(self, text="BASE_URL:", font=FONT_LABEL, text_color="white").place(x=x, y=y)
        self._url_entry = ctk.CTkEntry(self, width=380, height=30, font=FONT_BODY)
        self._url_entry.place(x=x, y=y + 22)
        self._url_entry.configure(state="disabled")
        y += gap  # 140

        # API_KEY
        ctk.CTkLabel(self, text="API_KEY:", font=FONT_LABEL, text_color="white").place(x=x, y=y)
        self._key_entry = ctk.CTkEntry(self, width=380, height=30, font=FONT_BODY, show="*")
        self._key_entry.place(x=x, y=y + 22)
        self._key_entry.configure(placeholder_text="sk-...")
        self._key_entry.bind("<KeyRelease>", lambda _e: self._update_next_state())
        y += gap  # 200

        # 默认模型 (ANTHROPIC_MODEL, pre-filled from provider, editable)
        ctk.CTkLabel(self, text="默认模型:", font=FONT_LABEL, text_color="white").place(x=x, y=y)
        self._model_entry = ctk.CTkEntry(self, width=380, height=30, font=FONT_BODY)
        self._model_entry.place(x=x, y=y + 22)
        y += gap  # 260

        # 高级配置 toggle
        self._adv_toggle = ctk.CTkLabel(
            self, text="▶ 高级配置",
            font=("Segoe UI", 12), text_color="#d4a574", cursor="hand2",
        )
        self._adv_toggle.place(x=x, y=y)  # y=260
        self._adv_toggle.bind("<Button-1>", lambda _e: self._toggle_advanced())

        # Advanced fields — Opus 模型 (ANTHROPIC_DEFAULT_OPUS_MODEL)
        self._opus_label = ctk.CTkLabel(self, text="Opus 模型:", font=FONT_LABEL, text_color="white")
        self._opus_entry = ctk.CTkEntry(self, width=380, height=30, font=FONT_BODY)

        # Advanced fields — Sonnet 模型 (ANTHROPIC_DEFAULT_SONNET_MODEL)
        self._sonnet_label = ctk.CTkLabel(self, text="Sonnet 模型:", font=FONT_LABEL, text_color="white")
        self._sonnet_entry = ctk.CTkEntry(self, width=380, height=30, font=FONT_BODY)

        # Advanced fields — Haiku 模型 (ANTHROPIC_DEFAULT_HAIKU_MODEL)
        self._haiku_label = ctk.CTkLabel(self, text="Haiku 模型:", font=FONT_LABEL, text_color="white")
        self._haiku_entry = ctk.CTkEntry(self, width=380, height=30, font=FONT_BODY)

        # 测试 button + result (initially at collapsed position)
        self._test_btn = ctk.CTkButton(
            self, text="测试连接",
            width=100, height=28,
            font=FONT_BTN, fg_color=ACCENT, hover_color="#c4694a",
            command=self._test,
        )
        self._test_btn.place(x=x, y=self._TEST_Y_COLLAPSED)

        self._test_label = ctk.CTkLabel(
            self, text="", font=FONT_STEP_DETAIL, text_color=TEXT_MUTED,
        )
        self._test_label.place(x=240, y=self._TEST_Y_COLLAPSED + 5)

        # 下一步 button
        self._btn_next = ctk.CTkButton(
            self, text="下一步",
            width=140, height=40,
            font=FONT_BTN_BOLD, fg_color=ACCENT, hover_color="#c4694a",
            text_color="white",
            state="disabled",
            command=self._finish,
        )
        self._btn_next.place(relx=0.5, rely=0.85, anchor="center")

        # 上一步 link
        back_label = ctk.CTkLabel(
            self, text="上一步",
            font=("Segoe UI", 13), text_color="#d4a574", cursor="hand2",
        )
        back_label.place(x=320 - 80 - 60, rely=0.85, anchor="e")
        back_label.bind("<Button-1>", lambda _e: self._go_back())

    def _go_back(self) -> None:
        if self._advanced_visible:
            self._collapse_advanced()
        self._wizard._show_page(PAGE_MODEL_SELECT)

    def set_provider(self, provider: dict) -> None:
        self._provider_data = provider
        name = provider.get("name", "")
        self._title.configure(text=f"配置 {name}")

        # Fill BASE_URL (read-only)
        self._url_entry.configure(state="normal")
        self._url_entry.delete(0, "end")
        self._url_entry.insert(0, provider.get("base_url", ""))
        self._url_entry.configure(state="disabled")

        # Clear API key
        self._key_entry.delete(0, "end")

        # Fill model fields from provider preset
        def _set_entry(entry: ctk.CTkEntry, value: str) -> None:
            entry.delete(0, "end")
            entry.insert(0, value)

        _set_entry(self._model_entry, provider.get("default_model", ""))
        _set_entry(self._opus_entry, provider.get("opus_model", ""))
        _set_entry(self._sonnet_entry, provider.get("sonnet_model", ""))
        _set_entry(self._haiku_entry, provider.get("haiku_model", ""))

        self._test_label.configure(text="")
        self._btn_next.configure(state="disabled")

        # Collapse advanced section when switching provider
        if self._advanced_visible:
            self._collapse_advanced()

    def _toggle_advanced(self) -> None:
        if self._advanced_visible:
            self._collapse_advanced()
        else:
            self._expand_advanced()

    def _expand_advanced(self) -> None:
        self._advanced_visible = True
        self._adv_toggle.configure(text="▼ 高级配置")
        x = 130
        self._opus_label.place(x=x, y=self._ADV_OPUS_Y)
        self._opus_entry.place(x=x, y=self._ADV_OPUS_Y + 22)
        self._sonnet_label.place(x=x, y=self._ADV_SONNET_Y)
        self._sonnet_entry.place(x=x, y=self._ADV_SONNET_Y + 22)
        self._haiku_label.place(x=x, y=self._ADV_HAIKU_Y)
        self._haiku_entry.place(x=x, y=self._ADV_HAIKU_Y + 22)
        self._test_btn.place(x=x, y=self._TEST_Y_EXPANDED)
        self._test_label.place(x=240, y=self._TEST_Y_EXPANDED + 5)
        self._wizard.geometry(f"{WIN_W}x{self._WIN_H_EXPANDED}")

    def _collapse_advanced(self) -> None:
        self._advanced_visible = False
        self._adv_toggle.configure(text="▶ 高级配置")
        self._opus_label.place_forget()
        self._opus_entry.place_forget()
        self._sonnet_label.place_forget()
        self._sonnet_entry.place_forget()
        self._haiku_label.place_forget()
        self._haiku_entry.place_forget()
        x = 130
        self._test_btn.place(x=x, y=self._TEST_Y_COLLAPSED)
        self._test_label.place(x=240, y=self._TEST_Y_COLLAPSED + 5)
        self._wizard.geometry(f"{WIN_W}x{self._WIN_H_NORMAL}")

    def _update_next_state(self) -> None:
        has_key = bool(self._key_entry.get().strip())
        self._btn_next.configure(state="normal" if has_key else "disabled")

    def _test(self) -> None:
        base_url = self._url_entry.get().strip()
        api_key = self._key_entry.get().strip()
        if not api_key:
            self._test_label.configure(text="请输入 API Key", text_color=ERROR_COL)
            return

        self._test_label.configure(text="测试中...", text_color=ACCENT)
        self._test_btn.configure(state="disabled")

        test_path = self._provider_data.get("test_path", "/v1/messages")
        model_id = self._model_entry.get().strip() or self._provider_data.get("default_model", "")

        def _do() -> None:
            ok, msg = test_connection(base_url, api_key, model_id, test_path)
            self._wizard._q.put({"type": "test_result", "ok": ok, "msg": msg})

        threading.Thread(target=_do, daemon=True).start()

    def show_test_result(self, ok: bool, msg: str) -> None:
        if ok:
            self._test_label.configure(text=msg, text_color=SUCCESS)
        else:
            self._test_label.configure(text=f"失败: {msg}", text_color=ERROR_COL)
        self._test_btn.configure(state="normal")

    def _finish(self) -> None:
        base_url = self._url_entry.get().strip()
        api_key = self._key_entry.get().strip()
        default_model = self._model_entry.get().strip()
        if base_url and api_key:
            provider_name = self._provider_data.get("name", "")
            opus = self._opus_entry.get().strip() or default_model
            sonnet = self._sonnet_entry.get().strip() or default_model
            haiku = self._haiku_entry.get().strip() or default_model
            save_model_config(base_url, api_key, sonnet, opus, haiku, provider_name, default_model)
            report_success(provider_name)
        if self._advanced_visible:
            self._collapse_advanced()
        self._wizard._show_page(PAGE_COMPLETE)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Custom Provider Page — manually configure any provider
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class CustomProviderPage(ctk.CTkFrame):
    _WIN_H_NORMAL = 500
    _WIN_H_EXPANDED = 700
    _TEST_Y_COLLAPSED = 330
    _TEST_Y_EXPANDED = 510
    _ADV_OPUS_Y = 330
    _ADV_SONNET_Y = 388
    _ADV_HAIKU_Y = 446
    _NAV_Y_COLLAPSED = 390
    _NAV_Y_EXPANDED = 575

    def __init__(self, parent: ctk.CTkFrame, wizard: InstallerWindow) -> None:
        super().__init__(parent, fg_color=PANEL_BG)
        self._wizard = wizard
        self._advanced_visible = False

        ctk.CTkLabel(
            self, text="自定义供应商",
            font=FONT_PAGE_TITLE, text_color="white",
        ).place(relx=0.5, y=18, anchor="n")

        ctk.CTkLabel(
            self, text="手动配置第三方 API 供应商信息",
            font=FONT_BODY, text_color=TEXT_MUTED,
        ).place(relx=0.5, y=44, anchor="n")

        y = 70
        gap = 58
        x = 130

        # 供应商名称
        ctk.CTkLabel(self, text="供应商名称:", font=FONT_LABEL, text_color="white").place(x=x, y=y)
        self._name_entry = ctk.CTkEntry(self, width=380, height=30, font=FONT_BODY)
        self._name_entry.place(x=x, y=y + 22)
        self._name_entry.configure(placeholder_text="例如：OpenRouter")
        self._name_entry.bind("<KeyRelease>", lambda _e: self._update_next_state())
        y += gap

        # BASE_URL
        ctk.CTkLabel(self, text="BASE_URL:", font=FONT_LABEL, text_color="white").place(x=x, y=y)
        self._url_entry = ctk.CTkEntry(self, width=380, height=30, font=FONT_BODY)
        self._url_entry.place(x=x, y=y + 22)
        self._url_entry.configure(placeholder_text="https://api.example.com/v1")
        self._url_entry.bind("<KeyRelease>", lambda _e: self._update_next_state())
        y += gap

        # API_KEY
        ctk.CTkLabel(self, text="API_KEY:", font=FONT_LABEL, text_color="white").place(x=x, y=y)
        self._key_entry = ctk.CTkEntry(self, width=380, height=30, font=FONT_BODY, show="*")
        self._key_entry.place(x=x, y=y + 22)
        self._key_entry.configure(placeholder_text="sk-...")
        self._key_entry.bind("<KeyRelease>", lambda _e: self._update_next_state())
        y += gap

        # 默认模型 (ANTHROPIC_MODEL)
        ctk.CTkLabel(self, text="默认模型:", font=FONT_LABEL, text_color="white").place(x=x, y=y)
        self._model_entry = ctk.CTkEntry(self, width=380, height=30, font=FONT_BODY)
        self._model_entry.place(x=x, y=y + 22)
        self._model_entry.configure(placeholder_text="例如：claude-sonnet-4-5")
        self._model_entry.bind("<KeyRelease>", lambda _e: self._update_next_state())
        y += gap  # y=302

        # 高级配置 toggle
        self._adv_toggle = ctk.CTkLabel(
            self, text="▶ 高级配置",
            font=("Segoe UI", 12), text_color="#d4a574", cursor="hand2",
        )
        self._adv_toggle.place(x=x, y=y)  # y=302
        self._adv_toggle.bind("<Button-1>", lambda _e: self._toggle_advanced())

        # Advanced fields — Opus 模型 (ANTHROPIC_DEFAULT_OPUS_MODEL)
        self._opus_label = ctk.CTkLabel(self, text="Opus 模型:", font=FONT_LABEL, text_color="white")
        self._opus_entry = ctk.CTkEntry(self, width=380, height=30, font=FONT_BODY)
        self._opus_entry.configure(placeholder_text="例如：claude-opus-4-5")

        # Advanced fields — Sonnet 模型 (ANTHROPIC_DEFAULT_SONNET_MODEL)
        self._sonnet_label = ctk.CTkLabel(self, text="Sonnet 模型:", font=FONT_LABEL, text_color="white")
        self._sonnet_entry = ctk.CTkEntry(self, width=380, height=30, font=FONT_BODY)
        self._sonnet_entry.configure(placeholder_text="例如：claude-sonnet-4-5")

        # Advanced fields — Haiku 模型 (ANTHROPIC_DEFAULT_HAIKU_MODEL)
        self._haiku_label = ctk.CTkLabel(self, text="Haiku 模型:", font=FONT_LABEL, text_color="white")
        self._haiku_entry = ctk.CTkEntry(self, width=380, height=30, font=FONT_BODY)
        self._haiku_entry.configure(placeholder_text="例如：claude-haiku-4-5")

        # 测试 button + result (initially at collapsed position)
        self._test_btn = ctk.CTkButton(
            self, text="测试连接",
            width=100, height=28,
            font=FONT_BTN, fg_color=ACCENT, hover_color="#c4694a",
            command=self._test,
        )
        self._test_btn.place(x=x, y=self._TEST_Y_COLLAPSED)

        self._test_label = ctk.CTkLabel(
            self, text="", font=FONT_STEP_DETAIL, text_color=TEXT_MUTED,
        )
        self._test_label.place(x=240, y=self._TEST_Y_COLLAPSED + 5)

        # 下一步 button + 上一步 link (与其他页面样式一致)
        self._btn_next = ctk.CTkButton(
            self, text="下一步",
            width=140, height=40,
            font=FONT_BTN_BOLD, fg_color=ACCENT, hover_color="#c4694a",
            text_color="white",
            state="disabled",
            command=self._finish,
        )
        self._btn_next.place(relx=0.5, y=self._NAV_Y_COLLAPSED, anchor="center")

        self._back_label = ctk.CTkLabel(
            self, text="上一步",
            font=("Segoe UI", 13), text_color="#d4a574", cursor="hand2",
        )
        self._back_label.place(x=320 - 80 - 60, y=self._NAV_Y_COLLAPSED, anchor="e")
        self._back_label.bind("<Button-1>", lambda _e: self._go_back())

    def _on_show(self) -> None:
        if self._advanced_visible:
            self._collapse_advanced()

    def _toggle_advanced(self) -> None:
        if self._advanced_visible:
            self._collapse_advanced()
        else:
            self._expand_advanced()

    def _expand_advanced(self) -> None:
        self._advanced_visible = True
        self._adv_toggle.configure(text="▼ 高级配置")
        x = 130
        self._opus_label.place(x=x, y=self._ADV_OPUS_Y)
        self._opus_entry.place(x=x, y=self._ADV_OPUS_Y + 22)
        self._sonnet_label.place(x=x, y=self._ADV_SONNET_Y)
        self._sonnet_entry.place(x=x, y=self._ADV_SONNET_Y + 22)
        self._haiku_label.place(x=x, y=self._ADV_HAIKU_Y)
        self._haiku_entry.place(x=x, y=self._ADV_HAIKU_Y + 22)
        self._test_btn.place(x=x, y=self._TEST_Y_EXPANDED)
        self._test_label.place(x=240, y=self._TEST_Y_EXPANDED + 5)
        self._btn_next.place(relx=0.5, y=self._NAV_Y_EXPANDED, anchor="center")
        self._back_label.place(x=320 - 80 - 60, y=self._NAV_Y_EXPANDED, anchor="e")
        self._wizard.geometry(f"{WIN_W}x{self._WIN_H_EXPANDED}")

    def _collapse_advanced(self) -> None:
        self._advanced_visible = False
        self._adv_toggle.configure(text="▶ 高级配置")
        self._opus_label.place_forget()
        self._opus_entry.place_forget()
        self._sonnet_label.place_forget()
        self._sonnet_entry.place_forget()
        self._haiku_label.place_forget()
        self._haiku_entry.place_forget()
        x = 130
        self._test_btn.place(x=x, y=self._TEST_Y_COLLAPSED)
        self._test_label.place(x=240, y=self._TEST_Y_COLLAPSED + 5)
        self._btn_next.place(relx=0.5, y=self._NAV_Y_COLLAPSED, anchor="center")
        self._back_label.place(x=320 - 80 - 60, y=self._NAV_Y_COLLAPSED, anchor="e")
        self._wizard.geometry(f"{WIN_W}x{self._WIN_H_NORMAL}")

    def _go_back(self) -> None:
        if self._advanced_visible:
            self._collapse_advanced()
        self._wizard._show_page(PAGE_MODEL_SELECT)

    def _update_next_state(self) -> None:
        filled = all([
            self._name_entry.get().strip(),
            self._url_entry.get().strip(),
            self._key_entry.get().strip(),
            self._model_entry.get().strip(),
        ])
        self._btn_next.configure(state="normal" if filled else "disabled")

    def _test(self) -> None:
        base_url = self._url_entry.get().strip()
        api_key = self._key_entry.get().strip()
        model_id = self._model_entry.get().strip()

        if not all([base_url, api_key, model_id]):
            self._test_label.configure(text="请填写所有字段", text_color=ERROR_COL)
            return

        self._test_label.configure(text="测试中...", text_color=ACCENT)
        self._test_btn.configure(state="disabled")

        def _do() -> None:
            ok, msg = test_connection(base_url, api_key, model_id, "/v1/messages")
            self._wizard._q.put({"type": "custom_test_result", "ok": ok, "msg": msg})

        threading.Thread(target=_do, daemon=True).start()

    def show_test_result(self, ok: bool, msg: str) -> None:
        if ok:
            self._test_label.configure(text=msg, text_color=SUCCESS)
        else:
            self._test_label.configure(text=f"失败: {msg}", text_color=ERROR_COL)
        self._test_btn.configure(state="normal")

    def _finish(self) -> None:
        base_url = self._url_entry.get().strip()
        api_key = self._key_entry.get().strip()
        model = self._model_entry.get().strip()
        if base_url and api_key and model:
            opus = self._opus_entry.get().strip() or model
            sonnet = self._sonnet_entry.get().strip() or model
            haiku = self._haiku_entry.get().strip() or model
            save_model_config(base_url, api_key, sonnet, opus, haiku, "自定义", model)
            report_success("自定义")
        if self._advanced_visible:
            self._collapse_advanced()
        self._wizard._show_page(PAGE_COMPLETE)




class CompletePage(ctk.CTkFrame):
    def __init__(self, parent: ctk.CTkFrame, wizard: InstallerWindow) -> None:
        super().__init__(parent, fg_color=PANEL_BG)

        ctk.CTkLabel(
            self, text="\u2713",
            font=("Segoe UI", 48), text_color=SUCCESS,
        ).place(relx=0.5, y=30, anchor="n")

        ctk.CTkLabel(
            self, text="\u5b89\u88c5\u5b8c\u6210",
            font=FONT_PAGE_TITLE, text_color="white",
        ).place(relx=0.5, y=100, anchor="n")

        ctk.CTkLabel(
            self, text="Claude Code 已就绪。",
            font=FONT_BODY, text_color=TEXT_LIGHT,
        ).place(relx=0.5, y=135, anchor="n")

        ctk.CTkLabel(
            self, text='在任意文件夹上/内单击右键，选择"从这里启动 Claude Code"，开动起来！',
            font=FONT_BODY, text_color=TEXT_LIGHT, wraplength=440,
        ).place(relx=0.5, y=165, anchor="n")

        ctk.CTkButton(
            self, text="\u5b8c\u6210",
            width=140, height=38,
            font=FONT_BTN_BOLD, fg_color=ACCENT, hover_color="#c4694a",
            text_color="white",
            command=wizard.destroy,
        ).place(relx=0.5, rely=0.85, anchor="center")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Uninstall Page
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class UninstallPage(ctk.CTkFrame):
    def __init__(self, parent: ctk.CTkFrame, wizard: InstallerWindow) -> None:
        super().__init__(parent, fg_color=PANEL_BG)
        self._wizard = wizard

        ctk.CTkLabel(
            self, text="选择要卸载的组件",
            font=FONT_PAGE_TITLE, text_color="white",
        ).place(relx=0.5, y=24, anchor="n")

        items = [
            (STEP_NODE,   "Node.js"),
            (STEP_GIT,    "Git"),
            (STEP_CLAUDE, "Claude Code"),
            (STEP_PATH,   "系统环境变量"),
            (STEP_MENU,   "右键菜单"),
            (STEP_MODEL,  "模型配置"),
        ]
        self._vars: dict[int, ctk.BooleanVar] = {}
        list_frame = ctk.CTkFrame(self, fg_color="transparent", width=260)
        list_frame.place(relx=0.5, y=70, anchor="n")
        for step, label in items:
            var = ctk.BooleanVar(value=True)
            self._vars[step] = var
            ctk.CTkCheckBox(
                list_frame, text=label, variable=var,
                font=FONT_BODY, text_color="white",
                fg_color=ACCENT, hover_color="#c4694a", border_color="#3a3a44",
            ).pack(anchor="w", pady=4)

        ctk.CTkButton(
            self, text="卸载", width=120, height=40,
            font=FONT_BTN_BOLD, fg_color="#4a1d1d", hover_color="#5c2727",
            text_color="white",
            command=self._confirm,
        ).place(relx=0.35, rely=0.85, anchor="center")

        ctk.CTkButton(
            self, text="取消", width=120, height=40,
            font=FONT_BTN_BOLD, fg_color="#45454f", hover_color="#55555f",
            text_color="white",
            command=self._cancel,
        ).place(relx=0.65, rely=0.85, anchor="center")

    def _confirm(self) -> None:
        selected = {s for s, v in self._vars.items() if v.get()}
        if not selected:
            return
        self._wizard._show_page(PAGE_INSTALL)
        self._wizard._pages[PAGE_INSTALL].start_uninstall(  # type: ignore
            self._wizard._worker, selected,
        )

    def _cancel(self) -> None:
        self._wizard._show_page(PAGE_WELCOME)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Update Page — in-app download + apply update
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class UpdatePage(ctk.CTkFrame):
    def __init__(self, parent: ctk.CTkFrame, wizard: InstallerWindow) -> None:
        super().__init__(parent, fg_color=PANEL_BG)
        self._wizard = wizard
        self._new_exe_path: str | None = None
        self._download_thread: threading.Thread | None = None
        self._cancelled = False
        self._update_q: queue.Queue = queue.Queue()

        self._title_label = ctk.CTkLabel(
            self, text="正在下载更新...",
            font=FONT_PAGE_TITLE, text_color="white",
        )
        self._title_label.place(relx=0.5, y=60, anchor="center")

        self._progress = ctk.CTkProgressBar(
            self, width=560, height=14,
            fg_color="#45454f", progress_color=ACCENT,
        )
        self._progress.set(0)
        self._progress.place(relx=0.5, y=120, anchor="center")

        self._status_label = ctk.CTkLabel(
            self, text="正在准备下载...",
            font=FONT_STEP_DETAIL, text_color=TEXT_MUTED,
        )
        self._status_label.place(relx=0.5, y=150, anchor="center")

        self._restart_btn = ctk.CTkButton(
            self, text="重启软件完成更新",
            width=200, height=44,
            font=FONT_BTN_BOLD, fg_color="#ff8c00", hover_color="#e07800",
            text_color="white",
            command=self._apply,
        )
        # Hidden until download completes

        # Cancel button
        self._cancel_lbl = ctk.CTkLabel(
            self, text="取消",
            font=FONT_STEP_DETAIL, text_color=TEXT_MUTED, cursor="hand2",
        )
        self._cancel_lbl.place(relx=0.5, y=280, anchor="center")
        self._cancel_lbl.bind("<Button-1>", lambda _: self._cancel())

    def start_download(self, info: dict) -> None:
        """Called before navigating to this page. Kicks off the download thread."""
        self._cancelled = False
        self._new_exe_path = None
        self._title_label.configure(text="正在下载更新...")
        self._progress.set(0)
        self._status_label.configure(text="正在准备下载...", text_color=TEXT_MUTED)
        self._restart_btn.place_forget()

        url = info.get("download_url", "")
        sha256 = info.get("sha256", "")

        self._download_thread = threading.Thread(
            target=self._do_download, args=(url, sha256), daemon=True,
        )
        self._download_thread.start()
        self.after(100, self._poll_update_q)

    def _do_download(self, url: str, sha256: str) -> None:
        def progress_cb(downloaded: int, total: int) -> None:
            self._update_q.put({"type": "progress", "downloaded": downloaded, "total": total})

        try:
            exe_path = download_update(url, sha256, progress_cb)
            if not self._cancelled:
                self._update_q.put({"type": "done", "exe": exe_path})
        except Exception as exc:
            if not self._cancelled:
                self._update_q.put({"type": "error", "msg": str(exc)})

    def _poll_update_q(self) -> None:
        try:
            while True:
                msg = self._update_q.get_nowait()
                mtype = msg.get("type")

                if mtype == "progress":
                    downloaded = msg.get("downloaded", 0)
                    total = msg.get("total", 0)
                    if total > 0:
                        ratio = downloaded / total
                        self._progress.set(ratio)
                        dl_mb = downloaded / 1048576
                        total_mb = total / 1048576
                        pct = int(ratio * 100)
                        self._status_label.configure(
                            text=f"{pct}%  ({dl_mb:.1f} MB / {total_mb:.1f} MB)",
                            text_color=TEXT_MUTED,
                        )

                elif mtype == "done":
                    self._new_exe_path = msg.get("exe")
                    self._title_label.configure(text="下载完成！")
                    self._progress.set(1)
                    self._status_label.configure(
                        text="验证完成，点击下方按钮重启软件完成更新",
                        text_color=SUCCESS,
                    )
                    self._restart_btn.place(relx=0.5, y=210, anchor="center")
                    return  # stop polling

                elif mtype == "error":
                    import logging
                    logging.getLogger("installer").error(f"download error: {msg.get('msg', '')}")
                    self._title_label.configure(text="下载失败")
                    self._status_label.configure(
                        text="更新下载失败，请检查网络后重试",
                        text_color=ERROR_COL,
                    )
                    return  # stop polling

        except queue.Empty:
            pass

        if not self._cancelled:
            self.after(100, self._poll_update_q)

    def _apply(self) -> None:
        if self._new_exe_path:
            apply_update(self._new_exe_path)

    def _cancel(self) -> None:
        self._cancelled = True
        self._wizard._show_page(PAGE_WELCOME)
