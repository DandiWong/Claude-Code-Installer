"""
All magic values in one place: registry keys, download URLs, command strings.
"""

APP_NAME = "Claude Code 懒人免翻墙一键安装包"
APP_VERSION = "1.1"  # 由 build.bat 自动维护，勿手动修改

# --- Download URLs ---
# Node.js fallback version (used only if latest-version lookup fails)
# Primary:  npmmirror.com  (Taobao/Alibaba maintained, CN-optimised)
# Fallback: nodejs.org official
NODE_VERSION = "20.11.0"
NODE_FILENAME = f"node-v{NODE_VERSION}-x64.msi"
NODE_DOWNLOAD_URLS = [
    f"https://npmmirror.com/mirrors/node/v{NODE_VERSION}/node-v{NODE_VERSION}-x64.msi",
    f"https://nodejs.org/dist/v{NODE_VERSION}/node-v{NODE_VERSION}-x64.msi",
]

# Git fallback version (used only if latest-version lookup fails)
# Primary:  TUNA mirror (Tsinghua University, reliable in CN)
# Fallback: GitHub releases
GIT_VERSION = "2.44.0"
GIT_FILENAME = f"Git-{GIT_VERSION}-64-bit.exe"
GIT_DOWNLOAD_URLS = [
    (
        f"https://mirrors.tuna.tsinghua.edu.cn/github-release/"
        f"git-for-windows/git/LatestRelease/{GIT_FILENAME}"
    ),
    (
        f"https://github.com/git-for-windows/git/releases/download/"
        f"v{GIT_VERSION}.windows.1/{GIT_FILENAME}"
    ),
]

# --- npm registry ---
# Official first (has platform-specific sub-packages), CN mirrors as fallback.
NPM_REGISTRIES = [
    ("https://registry.npmjs.org", "official registry"),
    ("https://mirrors.cloud.tencent.com/npm/", "Tencent Cloud (CN)"),
    ("https://repo.huaweicloud.com/repository/npm/", "Huawei Cloud (CN)"),
]

# --- Registry keys ---
# Right-click on a folder
REG_DIR_SHELL = r"SOFTWARE\Classes\Directory\shell\ClaudeCode"
REG_DIR_SHELL_CMD = r"SOFTWARE\Classes\Directory\shell\ClaudeCode\command"

# Right-click on folder background (inside a folder)
REG_BG_SHELL = r"SOFTWARE\Classes\Directory\Background\shell\ClaudeCode"
REG_BG_SHELL_CMD = r"SOFTWARE\Classes\Directory\Background\shell\ClaudeCode\command"

# Windows 11 classic context menu CLSID (HKCU)
REG_CLASSIC_CLSID = (
    r"Software\Classes\CLSID\{86ca1aa0-34aa-4e8b-a509-50c905bae2a2}"
    r"\InprocServer32"
)

# System PATH location (HKLM)
REG_SYSTEM_ENV = r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"

# --- Context menu display text ---
MENU_LABEL = "从这里启动 Claude Code"

# --- Shell commands for context menu entries ---
# %V = selected folder path (Directory\shell)
# %W = current folder path (Directory\Background\shell)
CMD_WT_DIR = r'wt.exe -d "%V" powershell -NoExit -Command claude'
CMD_WT_BG = r'wt.exe -d "%W" powershell -NoExit -Command claude'

# Fallback when Windows Terminal (wt.exe) is not installed
CMD_PS_DIR = r'powershell.exe -NoExit -Command "Set-Location ' "%V" '; claude"'
CMD_PS_BG = r'powershell.exe -NoExit -Command "Set-Location ' "%W" '; claude"'

# Windows Terminal executable path to check
WT_PATH_CANDIDATE = r"Microsoft\WindowsApps\wt.exe"  # relative to %LOCALAPPDATA%

# --- npm package ---
CLAUDE_NPM_PACKAGE = "@anthropic-ai/claude-code"

# --- Unified remote config (providers + info + claude_settings, encrypted) ---
CONFIG_URL = "https://api.claudecodeinstaller.com/config.json"
TEST_CONFIG_URL = "https://api-test.claudecodeinstaller.com/config.json"

# --- Plain info endpoint (version check, min_version, notice — backward compat) ---
INFO_URL = "https://api.claudecodeinstaller.com/info.json"
TEST_INFO_URL = "https://api-test.claudecodeinstaller.com/info.json"

# --- Usage reporting ---
REPORT_URL = "https://api.claudecodeinstaller.com/report"
TEST_REPORT_URL = "https://api-test.claudecodeinstaller.com/report"

# --- Test mode (set via --test CLI flag) ---
TEST_MODE = False


def get_config_url() -> str:
    return TEST_CONFIG_URL if TEST_MODE else CONFIG_URL


def get_info_url() -> str:
    return TEST_INFO_URL if TEST_MODE else INFO_URL


def get_report_url() -> str:
    return TEST_REPORT_URL if TEST_MODE else REPORT_URL

# --- Expected PATH entries (expand at runtime with os.environ) ---
PATH_NODE_PLACEHOLDER = r"%ProgramFiles%\nodejs"
PATH_NPM_PLACEHOLDER = r"%APPDATA%\npm"

# --- Icon filename stored next to the installer ---
ICON_REG_SUBKEY = r"LOCALAPPDATA"  # resolved at runtime
ICON_STORE_SUBDIR = "ClaudeCode"
ICON_FILENAME = "icon.ico"
