# Claude Code Installer for Windows

**官网：[claudecodeinstaller.com](https://claudecodeinstaller.com)**

<div align="center">

---

### 🤝 官方合作供应商

<a href="https://dragoncode.codes/register?ref=4Y363VZ6">
  <img src="https://img.shields.io/badge/DragonCode-推荐供应商-6366f1?style=for-the-badge&logo=anthropic&logoColor=white" alt="DragonCode" />
</a>

**本安装包与 [DragonCode](https://dragoncode.codes/register?ref=4Y363VZ6) 达成官方合作**

提供 Claude 原生 API 中转服务 · 支持 Opus / Sonnet / Haiku 全系列模型 · 国内直连无需翻墙

[立即注册 →](https://dragoncode.codes/register?ref=4Y363VZ6)

---

</div>

Windows10/11 专用，Claude Code 懒人一键安装包。

## 功能

| 步骤 | 操作 |
|------|------|
| 1 | 检测 / 安装 **Node.js、Git、Claude Code** |
| 2 | 添加缺失的 **PATH** 环境变量（nodejs + npm 全局 bin） |
| 3 | 根据引导配置模型供应商，或自定义配置 |
| 4 | 添加 **"从这里启动 Claude Code"** 到 Windows 资源管理器右键菜单 |

## 按钮说明

| 按钮 | 功能 |
|------|------|
| **开始安装** | 按顺序执行，自动跳过已安装项 |
| **修复** | 只重新运行当前检测失败的步骤 |
| **卸载** | 弹出选择对话框，勾选要移除的组件 |

> 注意：Node.js 和 Git 是系统级应用，卸载是永久性的，可能影响依赖它们的其他工具。

---

## 工程结构

```
claude-code-installer/
├── main.py                      入口，处理 UAC 提权
├── requirements.txt             Python 依赖
├── CLAUDE.md                    Claude Code 指令文件
│
├── app/                         ── 客户端（GUI 安装器）──
│   ├── constants.py             所有常量：URL、注册表键、APP_VERSION
│   ├── checks.py                依赖检测（纯函数）
│   ├── config_manager.py        模型配置读写（环境变量）
│   ├── crypto.py                端到端加密（AES-256-GCM + Ed25519）
│   ├── downloader.py            流式文件下载（支持镜像回退）
│   ├── installer.py             后台安装工作线程
│   ├── model_config.py          模型供应商连接测试
│   ├── releases.py              动态解析 Node.js / Git 最新版本
│   ├── registry.py              winreg 操作（PATH、右键菜单）
│   ├── updater.py               应用内自动更新（检查、下载、替换）
│   ├── usage_reporter.py        匿名使用统计上报
│   ├── window.py                CustomTkinter GUI（向导页面）
│   ├── assets/icon.ico          应用图标
│   ├── keys/public.json         加密密钥（由 server/crypto.py 生成，已 gitignore）
│   ├── keys/public.example.json 密钥文件模板
│   └── providers.json           备用供应商列表（打包进 exe）
│
├── server/                      ── 服务端（供应商 API）──
│   ├── server.py                HTTP 服务器（config、info、report、dashboard）
│   ├── crypto.py                加密 + 签名配置数据
│   ├── dashboard.html           使用统计仪表盘
│   ├── keys/                    Ed25519 + AES 密钥（首次部署时生成，已 gitignore）
│   └── data/
│       └── app_config.json      服务端配置（由 deploy.sh 生成，已 gitignore）
│
├── www/                         ── 网站（落地页）──
│   ├── index.html               版本号从 API 动态获取
│   └── assets/
│       ├── *.png, *.mp4         截图和演示视频
│       └── release/
│           └── ClaudeCodeInstaller.zip   发布包（自动生成）
│
├── scripts/                     ── 构建 & 部署 ──
│   ├── build.bat                Windows 构建脚本
│   ├── build.spec               PyInstaller 配置
│   ├── deploy.sh                部署脚本（在 scripts/ 下运行）
│   ├── config.json              部署配置（已 gitignore，从 config.example.json 复制）
│   ├── config.example.json      部署配置模板
│   └── _build_helpers.py        构建/部署 Python 辅助脚本
│
├── release/                     ── 发布归档（自动生成，已 gitignore）──
│   └── v1.0/
│       └── ClaudeCodeInstaller.zip
│
└── .github/workflows/build.yml  CI：推送时自动构建 exe
```

---

## 构建脚本 (`scripts/build.bat`)

在 **Windows** 上运行，构建可分发的 exe。

### 执行流程

```
1. 显示当前 APP_VERSION（如 v2.0）
2. 提示输入新版本号（如 2.1）— 留空保持不变
3. 安装 Python 依赖
4. 清理上次的 dist/ 和 build/
5. 更新 constants.py 和 config.json 中的版本号（如果提供了新版本）
6. PyInstaller 构建 → scripts/dist/ClaudeCodeInstaller.exe
```

### 要点

- **只产出 exe**，不生成 zip、不计算 SHA-256 — 这些由 deploy 负责。
- 不输入版本号也能正常构建（使用当前版本）。
- 开发期间放心运行，不会影响任何发布产物。

---

## 部署脚本 (`scripts/deploy.sh`)

在 `scripts/` 目录下运行，将所有变更推送到服务器。

### 用法

```bash
cd scripts
./deploy.sh          # 生产环境
./deploy.sh --test   # 测试环境
```

### 执行流程

```
1. 从 config.json 提取数据，生成 server/data/app_config.json
2. SSH 连接验证
3. [1/4] 加密密钥 — 不存在则生成，首次部署时上传
4. [2/4] 上传服务端文件（hash 比对，无变化则跳过）
        - server.py, crypto.py, dashboard.html
        - app_config.json
        - 安装远程 venv 依赖
5. [2.5/4] 发布包（仅当 dist/ 下有 exe 时）
        - 打包 exe → zip
        - 计算 SHA-256 → 更新 config.json
        - 归档到 release/vX.X/
        - 上传 zip 到网站
        - exe 与上次发布一致则跳过全部步骤
6. [2.5/4] 网站文件（hash 比对，无变化则跳过）
7. [3/4] 服务 — 注册 systemd，仅在服务端代码变更时重启
8. [4/4] 验证 — HTTP 健康检查
```

### 要点

- **Hash 比对**：每个文件都与远程比较，只有真正变化才上传。
- **智能重启**：仅在服务端代码（`.py`、`.html`）实际变更时重启服务。
- **发布自动检测**：`dist/` 下有 exe 就打包上传，没有就静默跳过。
- **网站自动检测**：`www/` 下有文件就检查上传。
- 无需 `--full`、`--release`、`--website` 等参数 — 全部自动。

---

## CI

推送到任意分支会触发 GitHub Actions（`build.yml`），在 `windows-latest` 上构建 `ClaudeCodeInstaller.exe` 并上传为 artifact。
