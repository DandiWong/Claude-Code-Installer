"""
Installation orchestration. Runs entirely in a background thread.
Communicates with the UI via a queue.Queue of typed message dicts.
"""

from __future__ import annotations
import logging
import os
import queue
import subprocess
import sys
import threading
from typing import Callable

from app import checks, downloader, registry
from app import releases

_log = logging.getLogger("installer")
from app.constants import (
    NODE_DOWNLOAD_URLS,
    NODE_FILENAME,
    GIT_DOWNLOAD_URLS,
    GIT_FILENAME,
    CLAUDE_NPM_PACKAGE,
    NPM_REGISTRIES,
    PATH_NODE_PLACEHOLDER,
    PATH_NPM_PLACEHOLDER,
)

# Step indices (1-based, matches UI rows)
STEP_NODE = 1
STEP_GIT = 2
STEP_CLAUDE = 3
STEP_PATH = 4
STEP_MENU = 5
STEP_MODEL = 6


class InstallerWorker:
    """
    Worker that performs installation tasks in a background thread.

    Message protocol (sent to self._q):
        {"type": "log",  "msg": str, "level": "info"|"warn"|"error"|"ok"}
        {"type": "step", "step": int, "status": "running"|"ok"|"error"|"pending"}
        {"type": "done", "success": bool}
        {"type": "progress", "step": int, "downloaded": int, "total": int}
    """

    def __init__(self, task_queue: queue.Queue) -> None:
        self._q = task_queue
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    # Public API (called from UI thread)
    # ------------------------------------------------------------------

    def start_full_install(self) -> None:
        self._start(self._run_full_install)

    def start_repair(self) -> None:
        self._start(self._run_repair)

    def start_uninstall(self, selection: set[int]) -> None:
        self._start(lambda: self._run_uninstall(selection))

    def start_model_config(
        self, base_url: str, api_key: str,
        sonnet: str, opus: str, haiku: str,
    ) -> None:
        self._start(lambda: self._save_model_config(
            base_url, api_key, sonnet, opus, haiku,
        ))

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ------------------------------------------------------------------
    # Thread launcher
    # ------------------------------------------------------------------

    def _start(self, target: Callable) -> None:
        if self.is_running():
            self._log("有操作正在运行中。", "warn")
            return
        self._thread = threading.Thread(target=target, daemon=True)
        self._thread.start()

    # ------------------------------------------------------------------
    # Workflows
    # ------------------------------------------------------------------

    def _run_full_install(self) -> None:
        success = True
        try:
            success &= self._install_node()
            success &= self._install_git()
            success &= self._install_claude_code()
            success &= self._set_environment_paths()
            success &= self._write_context_menu()
            # Step 6 (model config) is optional and triggered separately via UI
        except Exception as exc:
            self._log(f"未知错误: {exc}", "error")
            success = False
        self._q.put({"type": "done", "success": success})

    def _run_repair(self) -> None:
        success = True
        try:
            node_ok, _ = checks.check_node()
            if not node_ok:
                success &= self._install_node()

            git_ok, _ = checks.check_git()
            if not git_ok:
                success &= self._install_git()

            claude_ok, _ = checks.check_claude_code()
            if not claude_ok:
                success &= self._install_claude_code()

            missing = checks.check_path_entries()
            if missing:
                success &= self._set_environment_paths()

            if not checks.check_context_menu():
                success &= self._write_context_menu()
        except Exception as exc:
            self._log(f"修复时出错: {exc}", "error")
            success = False
        self._q.put({"type": "done", "success": success})

    def _run_uninstall(self, selection: set[int]) -> None:
        success = True
        try:
            if STEP_CLAUDE in selection:
                success &= self._uninstall_claude_code()
            if STEP_NODE in selection:
                success &= self._uninstall_node()
            if STEP_GIT in selection:
                success &= self._uninstall_git()
            if STEP_PATH in selection:
                success &= self._uninstall_path_entries()
            if STEP_MENU in selection:
                success &= self._uninstall_context_menu()
            if STEP_MODEL in selection:
                success &= self._uninstall_model_config()
        except Exception as exc:
            self._log(f"卸载时出错: {exc}", "error")
            success = False
        self._q.put({"type": "done", "success": success, "recheck": True})

    # ------------------------------------------------------------------
    # Individual installation steps
    # ------------------------------------------------------------------

    def _install_node(self) -> bool:
        self._step_status(STEP_NODE, "running")
        ok, ver = checks.check_node()
        if ok:
            self._log(f"Node.js 已安装: {ver}", "ok")
            self._step_status(STEP_NODE, "ok")
            self._detail(STEP_NODE, ver)
            return True

        node_filename = NODE_FILENAME
        node_urls = NODE_DOWNLOAD_URLS
        try:
            node_filename, node_urls = releases.latest_node_lts_x64_msi()
            self._log(f"已解析最新 Node.js LTS: {node_filename}")
        except Exception as exc:
            self._log(
                f"无法解析最新 Node.js LTS ({exc})，使用备用版本 {NODE_FILENAME}。",
                "warn",
            )

        # Extract version from filename
        import re
        node_ver_match = re.match(r"node-(v[\d.]+)", node_filename)
        node_ver = node_ver_match.group(1) if node_ver_match else ""
        if node_ver:
            self._detail(STEP_NODE, f"{node_ver} 下载中...")

        self._log(f"正在下载 Node.js {node_filename}...")
        try:
            msi_path = downloader.download_to_temp(
                node_urls,
                node_filename,
                log_cb=self._log,
                progress_cb=lambda d, t: self._progress(STEP_NODE, d, t),
            )
        except Exception as exc:
            self._log(f"下载失败: {exc}", "error")
            self._step_status(STEP_NODE, "error")
            return False

        self._log("正在安装 Node.js（可能需要一分钟）...")
        result = subprocess.run(
            ["msiexec", "/i", msi_path, "/qn", "/norestart"],
            capture_output=True,
        )
        if result.returncode != 0:
            self._log(
                f"Node.js 安装失败 (退出码 {result.returncode})。", "error"
            )
            self._step_status(STEP_NODE, "error")
            return False

        ok2, ver2 = checks.check_node()
        display_ver = ver2 if ok2 else node_ver or "Installed"
        self._log(f"Node.js 安装完成: {display_ver}", "ok")
        self._step_status(STEP_NODE, "ok")
        self._detail(STEP_NODE, display_ver)
        return True

    def _install_git(self) -> bool:
        self._step_status(STEP_GIT, "running")
        ok, ver = checks.check_git()
        if ok:
            self._log(f"Git 已安装: {ver}", "ok")
            self._step_status(STEP_GIT, "ok")
            self._detail(STEP_GIT, ver)
            return True

        git_filename = GIT_FILENAME
        git_urls = GIT_DOWNLOAD_URLS
        try:
            git_filename, git_urls = releases.latest_git_windows_x64_exe()
            self._log(f"已解析最新 Git for Windows: {git_filename}")
        except Exception as exc:
            self._log(
                f"无法解析最新 Git 版本 ({exc})，使用备用版本 {GIT_FILENAME}。",
                "warn",
            )

        # Extract version from filename
        import re
        mg = re.match(r"Git-([\d.]+)", git_filename)
        git_ver = f"v{mg.group(1)}" if mg else ""
        if git_ver:
            self._detail(STEP_GIT, f"{git_ver} 下载中...")

        self._log(f"正在下载 {git_filename}...")
        try:
            exe_path = downloader.download_to_temp(
                git_urls,
                git_filename,
                log_cb=self._log,
                progress_cb=lambda d, t: self._progress(STEP_GIT, d, t),
            )
        except Exception as exc:
            self._log(f"下载失败: {exc}", "error")
            self._step_status(STEP_GIT, "error")
            return False

        self._log("正在安装 Git（可能需要一分钟）...")
        result = subprocess.run(
            [exe_path, "/VERYSILENT", "/NORESTART", "/NOCANCEL", "/SP-"],
            capture_output=True,
        )
        if result.returncode != 0:
            self._log(f"Git 安装失败 (退出码 {result.returncode})。", "error")
            self._step_status(STEP_GIT, "error")
            return False

        ok2, ver2 = checks.check_git()
        display_ver = ver2 if ok2 else git_ver or "Installed"
        self._log(f"Git 安装完成: {display_ver}", "ok")
        self._step_status(STEP_GIT, "ok")
        self._detail(STEP_GIT, display_ver)
        return True

    _NPM_IDLE_TIMEOUT = 30          # seconds of no output before aborting
    _NPM_MAX_RETRIES = 3            # max retries per registry on idle timeout

    def _install_claude_code(self) -> bool:
        self._step_status(STEP_CLAUDE, "running")

        # Ensure PowerShell can execute npm-installed .ps1 scripts
        self._ensure_ps_execution_policy()

        ok, ver = checks.check_claude_code()
        if ok:
            self._log(f"Claude Code 已安装: {ver}", "ok")
            self._step_status(STEP_CLAUDE, "ok")
            self._detail(STEP_CLAUDE, ver)
            return True

        npm_path = checks.get_npm_path()
        if not npm_path:
            self._log("未找到 npm，请先安装 Node.js。", "error")
            self._step_status(STEP_CLAUDE, "error")
            self._detail(STEP_CLAUDE, "未找到 npm")
            return False

        # Test registry reachability before trying npm install
        reachable: list[tuple[str, str]] = []
        for url, label in NPM_REGISTRIES:
            self._log(f"正在检查源: {label}...")
            if self._check_npm_registry(url, CLAUDE_NPM_PACKAGE):
                reachable.append((url, label))
                self._log(f"源可用: {label}", "ok")
            else:
                self._log(f"源不可用: {label}，跳过。", "warn")

        if not reachable:
            self._log("所有 npm 源均不可达，请检查网络。", "error")
            self._step_status(STEP_CLAUDE, "error")
            self._detail(STEP_CLAUDE, "网络错误")
            return False

        success = False
        for registry_url, label in reachable:
            for attempt in range(1, self._NPM_MAX_RETRIES + 1):
                attempt_label = f"{label} 第{attempt}次" if attempt > 1 else label
                self._log(
                    f"运行中: npm install -g {CLAUDE_NPM_PACKAGE}  [registry: {attempt_label}]"
                )
                try:
                    popen_kwargs: dict = {
                        "stdout": subprocess.PIPE,
                        "stderr": subprocess.STDOUT,
                        "text": True,
                    }
                    # Windows: fix PATH, cwd, and hide console
                    if sys.platform == "win32":
                        popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
                        env = os.environ.copy()
                        # Inject Node.js into PATH — it was just installed but the
                        # current process inherited PATH before the install.
                        node_dir = os.path.join(
                            os.environ.get("ProgramFiles", r"C:\Program Files"), "nodejs"
                        )
                        if os.path.isdir(node_dir):
                            current_path = env.get("PATH", "")
                            if node_dir.lower() not in current_path.lower():
                                env["PATH"] = node_dir + os.pathsep + current_path
                        # Force x64 — on ARM64 Windows, npm would resolve the
                        # arm64 platform package which produces an incompatible
                        # claude.exe.
                        env["npm_config_arch"] = "x64"
                        popen_kwargs["env"] = env
                        # Avoid UNC path issues (e.g. Parallels shared folders)
                        popen_kwargs["cwd"] = os.environ.get(
                            "USERPROFILE", os.environ.get("TEMP", "C:\\")
                        )

                    process = subprocess.Popen(
                        [
                            npm_path,
                            "install",
                            "-g",
                            CLAUDE_NPM_PACKAGE,
                            "--registry",
                            registry_url,
                        ],
                        **popen_kwargs,
                    )
                    assert process.stdout is not None
                    timed_out = self._drain_npm_output(process, label)
                    if timed_out:
                        if attempt < self._NPM_MAX_RETRIES:
                            self._log(
                                f"  重试 ({attempt}/{self._NPM_MAX_RETRIES}) [{label}]…",
                                "warn",
                            )
                            continue
                        else:
                            self._log(
                                f"  [{label}] 重试 {self._NPM_MAX_RETRIES} 次均超时，切换下一个源。",
                                "warn",
                            )
                        break  # next registry

                    if process.returncode == 0:
                        success = True
                        break
                    self._log(
                        f"npm 安装失败 [{label}] (退出码 {process.returncode})，尝试下一个源…",
                        "warn",
                    )
                    break  # non-zero exit: no retry, next registry
                except Exception as exc:
                    self._log(f"npm 安装错误 [{label}]: {exc}", "warn")
                    break
            if success:
                break

        if not success:
            self._log("npm 安装失败，所有源均不可用。", "error")
            self._step_status(STEP_CLAUDE, "error")
            return False

        ok2, ver2 = checks.check_claude_code()
        if not ok2:
            # Fallback: read version from installed package.json
            appdata = os.environ.get("APPDATA", "")
            pkg_json = os.path.join(
                appdata, "npm", "node_modules", "@anthropic-ai",
                "claude-code", "package.json",
            )
            if os.path.isfile(pkg_json):
                try:
                    import json
                    with open(pkg_json, encoding="utf-8") as f:
                        ver2 = "v" + json.load(f).get("version", "")
                except Exception:
                    ver2 = "已安装"
            else:
                ver2 = "已安装"
        self._log(f"Claude Code 安装完成: {ver2}", "ok")
        self._detail(STEP_CLAUDE, ver2)
        self._step_status(STEP_CLAUDE, "ok")
        return True

    @staticmethod
    def _check_npm_registry(registry_url: str, package: str) -> bool:
        """
        Check if an npm registry is reachable AND has the target package.
        Queries the package metadata endpoint (GET /{package}) with a short timeout.
        """
        import urllib.request
        import urllib.error
        url = f"{registry_url.rstrip('/')}/{package}"
        try:
            req = urllib.request.Request(
                url,
                method="GET",
                headers={"User-Agent": "ClaudeCodeInstaller/1.0", "Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status == 200
        except Exception:
            return False

    def _drain_npm_output(self, process: subprocess.Popen, label: str) -> bool:
        """
        Read npm stdout with idle-based timeout using a reader thread.
        If no new output is received for _NPM_IDLE_TIMEOUT seconds, kill
        the process and return True (caller should retry).
        On Windows, npm spawns child processes that may keep the stdout pipe
        open after the main process exits. We detect this by checking
        process.poll() — if the main process is done, we stop waiting.
        Returns True if the process was killed due to idle timeout.
        """
        import time
        assert process.stdout is not None

        output_q: queue.Queue[str | None] = queue.Queue()

        def _reader() -> None:
            try:
                for line in process.stdout:
                    output_q.put(line)
            finally:
                output_q.put(None)  # sentinel: EOF

        reader_thread = threading.Thread(target=_reader, daemon=True)
        reader_thread.start()

        last_activity = time.monotonic()

        while True:
            try:
                line = output_q.get(timeout=1.0)
            except queue.Empty:
                # Main process already exited? Done — don't wait for child pipes.
                if process.poll() is not None:
                    break
                if time.monotonic() - last_activity > self._NPM_IDLE_TIMEOUT:
                    self._log(
                        f"npm install {self._NPM_IDLE_TIMEOUT}s 无新输出 [{label}]，中断。",
                        "warn",
                    )
                    process.kill()
                    process.wait()
                    reader_thread.join(timeout=5)
                    return True
                continue

            # Sentinel — EOF
            if line is None:
                break

            last_activity = time.monotonic()
            stripped = line.rstrip()
            if stripped:
                self._log(stripped)

        # Drain any remaining output in the queue
        while True:
            try:
                line = output_q.get(timeout=0.1)
                if line is not None:
                    stripped = line.rstrip()
                    if stripped:
                        self._log(stripped)
            except queue.Empty:
                break

        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            return True

        reader_thread.join(timeout=3)
        return False

    def _set_environment_paths(self) -> bool:
        self._step_status(STEP_PATH, "running")
        missing = checks.check_path_entries()

        if not missing:
            self._log("所有必要的 PATH 条目已存在。", "ok")
            self._step_status(STEP_PATH, "ok")
            self._detail(STEP_PATH, "完成")
            return True

        all_ok = True
        for directory in missing:
            self._log(f"正在添加到系统 PATH: {directory}")
            try:
                changed = registry.add_to_system_path(directory)
                if changed:
                    self._log(f"已添加: {directory}", "ok")
                else:
                    self._log(f"已存在: {directory}", "ok")
            except Exception as exc:
                self._log(f"添加失败 {directory}: {exc}", "error")
                all_ok = False

        if all_ok:
            self._step_status(STEP_PATH, "ok")
            self._detail(STEP_PATH, "完成")
        else:
            self._step_status(STEP_PATH, "error")
            self._detail(STEP_PATH, f"失败 ({len(missing)} 条路径)")
        return all_ok

    def _write_context_menu(self) -> bool:
        self._step_status(STEP_MENU, "running")
        self._log("正在写入右键菜单注册表...")
        try:
            registry.write_context_menu_entries()
            self._log("右键菜单已写入。", "ok")
            registry.set_classic_context_menu(True)
            self._log("已启用经典右键菜单 (Windows 11)。", "ok")
            registry.restart_explorer()
            self._log("资源管理器已重启，右键菜单生效。", "ok")
            self._step_status(STEP_MENU, "ok")
            self._detail(STEP_MENU, "已安装")
            return True
        except Exception as exc:
            self._log(f"右键菜单设置失败: {exc}", "error")
            self._step_status(STEP_MENU, "error")
            self._detail(STEP_MENU, "失败")
            return False

    def _save_model_config(
        self, base_url: str, api_key: str,
        sonnet: str, opus: str, haiku: str,
    ) -> None:
        self._step_status(STEP_MODEL, "running")
        self._log("正在保存模型配置...")
        try:
            from app import config_manager
            config_manager.save_model_config(base_url, api_key, sonnet, opus, haiku)
            from app import remote_config
            config_manager.apply_remote_settings(remote_config.get_claude_settings())
            self._log("模型配置已保存到 ~/.claude/settings.json", "ok")
            self._step_status(STEP_MODEL, "ok")
            config = config_manager.get_model_config()
            url = config.get("ANTHROPIC_BASE_URL", "")
            sn = config.get("ANTHROPIC_DEFAULT_SONNET_MODEL", "")
            detail = f"URL: {url}" if url else "已配置"
            if sn:
                detail += f" | {sn}"
            self._detail(STEP_MODEL, detail)
        except Exception as exc:
            self._log(f"保存模型配置失败: {exc}", "error")
            self._step_status(STEP_MODEL, "error")
            self._detail(STEP_MODEL, "失败")
        self._q.put({"type": "done", "success": True})

    # ------------------------------------------------------------------
    # Individual uninstallation steps
    # ------------------------------------------------------------------

    def _uninstall_node(self) -> bool:
        self._step_status(STEP_NODE, "running")
        self._log("正在卸载 Node.js...")
        uninstall_string = self._find_uninstall_string("Node.js")
        if uninstall_string:
            result = subprocess.run(
                ["msiexec", "/x", uninstall_string, "/qn", "/norestart"],
                capture_output=True,
            )
            if result.returncode == 0:
                self._log("Node.js 已卸载。", "ok")
                self._step_status(STEP_NODE, "pending")
                self._detail(STEP_NODE, "已卸载")
                return True
            self._log(
                f"msiexec exit {result.returncode} — trying wmic fallback...", "warn"
            )
        result = subprocess.run(
            [
                "wmic",
                "product",
                "where",
                "name like 'Node.js%'",
                "call",
                "uninstall",
                "/nointeractive",
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0 and "ReturnValue = 0" in result.stdout:
            self._log("Node.js 卸载失败或未找到。", "error")
            self._detail(STEP_NODE, "卸载失败")
            self._step_status(STEP_NODE, "pending")
            self._detail(STEP_NODE, "已卸载")
            return True
        self._log("Node.js 卸载失败或未找到。", "error")
        self._step_status(STEP_NODE, "error")
        self._detail(STEP_NODE, "卸载失败")
        return False

    def _uninstall_git(self) -> bool:
        self._step_status(STEP_GIT, "running")
        self._log("正在卸载 Git...")
        ok_before, _ = checks.check_git()
        if not ok_before:
            self._log("未检测到 Git。", "warn")
            self._step_status(STEP_GIT, "pending")
            self._detail(STEP_GIT, "已移除")
            return True

        uninstall_cmd = self._find_uninstall_command("Git")
        if uninstall_cmd:
            self._log("在注册表中找到 Git 卸载命令。")
            result = subprocess.run(
                [
                    "cmd",
                    "/c",
                    f"{uninstall_cmd} /VERYSILENT /NORESTART /SUPPRESSMSGBOXES",
                ],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                self._log(
                    f"注册表卸载命令退出码 {result.returncode}，尝试文件查找...",
                    "warn",
                )

        candidates = [
            os.path.join(
                os.environ.get("ProgramFiles", r"C:\Program Files"),
                "Git",
                "unins000.exe",
            ),
            os.path.join(
                os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                "Git",
                "unins000.exe",
            ),
            os.path.join(
                os.environ.get("LOCALAPPDATA", ""), "Programs", "Git", "unins000.exe"
            ),
        ]
        for uninstaller in candidates:
            if not uninstaller or not os.path.isfile(uninstaller):
                continue
            self._log(f"尝试卸载程序: {uninstaller}")
            result = subprocess.run(
                [uninstaller, "/VERYSILENT", "/NORESTART", "/SUPPRESSMSGBOXES"],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                break

        ok_after, _ = checks.check_git()
        if not ok_after:
            self._log("Git 已卸载。", "ok")
            self._step_status(STEP_GIT, "pending")
            self._detail(STEP_GIT, "已卸载")
            return True

        self._log("Git 卸载失败或需要重启/重新登录。", "error")
        self._step_status(STEP_GIT, "error")
        self._detail(STEP_GIT, "卸载失败")
        return False

    def _uninstall_claude_code(self) -> bool:
        self._step_status(STEP_CLAUDE, "running")
        self._log("正在卸载 Claude Code...")
        npm_path = checks.get_npm_path()
        if not npm_path:
            self._log("未找到 npm，无法卸载 Claude Code。", "error")
            self._step_status(STEP_CLAUDE, "error")
            self._detail(STEP_CLAUDE, "未找到 npm")
            return False
        result = subprocess.run(
            [npm_path, "uninstall", "-g", CLAUDE_NPM_PACKAGE],
            capture_output=True,
            text=True,
        )
        # Clean up everything npm leaves behind
        self._clean_npm_claude_leftovers()

        if result.returncode == 0 or not self._claude_code_exists():
            self._log("Claude Code 已卸载。", "ok")
            self._step_status(STEP_CLAUDE, "pending")
            self._detail(STEP_CLAUDE, "已卸载")
            return True
        self._log(f"Claude Code 卸载失败:\n{result.stderr.strip()}", "error")
        self._step_status(STEP_CLAUDE, "error")
        self._detail(STEP_CLAUDE, "卸载失败")
        return False

    @staticmethod
    def _claude_code_exists() -> bool:
        """Check if Claude Code package directory still exists."""
        pkg_dir = os.path.join(
            os.environ.get("APPDATA", ""),
            "npm", "node_modules", "@anthropic-ai", "claude-code",
        )
        return os.path.isdir(pkg_dir)

    @staticmethod
    def _clean_npm_claude_leftovers() -> None:
        """
        Remove all Claude Code artifacts that npm may leave behind:
        - shell wrappers (claude, claude.cmd, claude.ps1)
        - package directory (node_modules/@anthropic-ai/claude-code)
        - temp install dirs (node_modules/@anthropic-ai/.claude-code-*)
        - parent @anthropic-ai dir if empty after cleanup
        """
        import shutil
        npm_dir = os.path.join(os.environ.get("APPDATA", ""), "npm")
        if not os.path.isdir(npm_dir):
            return

        # Remove shell wrappers
        for ext in ("", ".cmd", ".ps1"):
            path = os.path.join(npm_dir, "claude" + ext)
            if os.path.isfile(path):
                try:
                    os.remove(path)
                except OSError:
                    pass

        # Remove package and temp directories
        scoped_dir = os.path.join(npm_dir, "node_modules", "@anthropic-ai")
        if os.path.isdir(scoped_dir):
            try:
                entries = os.listdir(scoped_dir)
            except OSError:
                return
            for entry in entries:
                entry_path = os.path.join(scoped_dir, entry)
                if os.path.isdir(entry_path):
                    try:
                        shutil.rmtree(entry_path, ignore_errors=True)
                    except OSError:
                        pass
            # Remove parent @anthropic-ai dir if now empty
            try:
                os.rmdir(scoped_dir)
            except OSError:
                pass

    def _uninstall_path_entries(self) -> bool:
        self._step_status(STEP_PATH, "running")
        self._log("正在移除本安装程序添加的系统环境变量条目...")
        from app.constants import PATH_NODE_PLACEHOLDER, PATH_NPM_PLACEHOLDER

        removed: list[str] = []
        failed: list[str] = []
        for placeholder in (PATH_NODE_PLACEHOLDER, PATH_NPM_PLACEHOLDER):
            expanded = os.path.expandvars(placeholder)
            try:
                changed = registry.remove_from_system_path(expanded)
                if changed:
                    self._log(f"已从系统环境变量移除: {expanded}", "ok")
                    removed.append(expanded)
                else:
                    self._log(f"不在系统环境变量中（跳过）: {expanded}")
            except Exception as exc:
                self._log(f"移除失败 {expanded}: {exc}", "error")
                failed.append(expanded)

        if failed:
            self._step_status(STEP_PATH, "error")
            self._detail(STEP_PATH, f"失败 ({len(failed)} 条)")
            return False

        self._step_status(STEP_PATH, "pending")
        self._detail(
            STEP_PATH,
            f"已移除 {len(removed)} 条" if removed else "无需移除",
        )
        return True

    def _uninstall_context_menu(self) -> bool:
        self._step_status(STEP_MENU, "running")
        self._log("正在移除右键菜单...")
        try:
            registry.remove_context_menu_entries()
            registry.set_classic_context_menu(False)
            registry.restart_explorer()
            self._log("右键菜单已移除。", "ok")
            self._step_status(STEP_MENU, "pending")
            self._detail(STEP_MENU, "已移除")
            return True
        except Exception as exc:
            self._log(f"移除右键菜单失败: {exc}", "error")
            self._step_status(STEP_MENU, "error")
            self._detail(STEP_MENU, "失败")
            return False

    def _uninstall_model_config(self) -> bool:
        self._step_status(STEP_MODEL, "running")
        self._log("正在移除模型配置...")
        try:
            from app import config_manager
            config_manager.clear_model_config()
            self._log("模型配置已移除。", "ok")
            self._step_status(STEP_MODEL, "pending")
            self._detail(STEP_MODEL, "已移除")
            return True
        except Exception as exc:
            self._log(f"移除模型配置失败: {exc}", "error")
            self._step_status(STEP_MODEL, "error")
            self._detail(STEP_MODEL, "失败")
            return False

    def _ensure_ps_execution_policy(self) -> None:
        check = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-ExecutionPolicy -Scope CurrentUser",
            ],
            capture_output=True,
            text=True,
        )
        policy = check.stdout.strip().lower()
        if policy in ("remotesigned", "unrestricted", "bypass"):
            return

        self._log(
            "Setting PowerShell execution policy to RemoteSigned (CurrentUser)..."
        )
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser -Force",
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            self._log("PowerShell 执行策略已设为 RemoteSigned。", "ok")
        else:
            self._log(
                f"Could not set execution policy: {result.stderr.strip() or result.stdout.strip()}",
                "warn",
            )

    @staticmethod
    def _find_uninstall_string(display_name_prefix: str) -> str | None:
        try:
            import winreg

            roots = [
                (winreg.HKEY_LOCAL_MACHINE,
                 r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
                (winreg.HKEY_LOCAL_MACHINE,
                 r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
            ]
            for hive, path in roots:
                try:
                    key = winreg.OpenKey(hive, path)
                except FileNotFoundError:
                    continue
                i = 0
                while True:
                    try:
                        subkey_name = winreg.EnumKey(key, i)
                        subkey = winreg.OpenKey(key, subkey_name)
                        try:
                            name, _ = winreg.QueryValueEx(subkey, "DisplayName")
                            if name.startswith(display_name_prefix):
                                return subkey_name
                        except FileNotFoundError:
                            pass
                        winreg.CloseKey(subkey)
                        i += 1
                    except OSError:
                        break
                winreg.CloseKey(key)
        except Exception:
            pass
        return None

    @staticmethod
    def _find_uninstall_command(display_name_prefix: str) -> str | None:
        try:
            import winreg

            roots = [
                (winreg.HKEY_LOCAL_MACHINE,
                 r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
                (winreg.HKEY_LOCAL_MACHINE,
                 r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
                (winreg.HKEY_CURRENT_USER,
                 r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
                (winreg.HKEY_CURRENT_USER,
                 r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
            ]

            for hive, path in roots:
                try:
                    key = winreg.OpenKey(hive, path)
                except FileNotFoundError:
                    continue

                i = 0
                while True:
                    try:
                        subkey_name = winreg.EnumKey(key, i)
                        subkey = winreg.OpenKey(key, subkey_name)
                        i += 1
                        try:
                            name, _ = winreg.QueryValueEx(subkey, "DisplayName")
                        except FileNotFoundError:
                            winreg.CloseKey(subkey)
                            continue

                        if not isinstance(name, str) or not name.startswith(
                            display_name_prefix
                        ):
                            winreg.CloseKey(subkey)
                            continue

                        try:
                            cmd, _ = winreg.QueryValueEx(subkey, "QuietUninstallString")
                        except FileNotFoundError:
                            try:
                                cmd, _ = winreg.QueryValueEx(subkey, "UninstallString")
                            except FileNotFoundError:
                                cmd = None

                        winreg.CloseKey(subkey)
                        if isinstance(cmd, str) and cmd.strip():
                            winreg.CloseKey(key)
                            return cmd.strip()
                    except OSError:
                        break

                winreg.CloseKey(key)
        except Exception:
            pass

        return None

    # ------------------------------------------------------------------
    # Queue helpers
    # ------------------------------------------------------------------

    def _log(self, msg: str, level: str = "info") -> None:
        self._q.put({"type": "log", "msg": msg, "level": level})
        # Mirror to file logger
        log_level = {
            "ok": logging.INFO,
            "info": logging.INFO,
            "warn": logging.WARNING,
            "error": logging.ERROR,
        }.get(level, logging.INFO)
        _log.log(log_level, msg)

    def _step_status(self, step: int, status: str) -> None:
        self._q.put({"type": "step", "step": step, "status": status})

    def _detail(self, step: int, text: str) -> None:
        self._q.put({"type": "detail", "step": step, "text": text})

    def _progress(self, step: int, downloaded: int, total: int) -> None:
        self._q.put(
            {"type": "progress", "step": step, "downloaded": downloaded, "total": total}
        )
