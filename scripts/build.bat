@echo off
chcp 65001 >nul 2>&1
setlocal EnableDelayedExpansion

:: Switch to scripts directory
cd /d "%~dp0"

:: Check Python
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python not found. Install Python 3.11+ and add to PATH.
    pause
    exit /b 1
)

:: Read current version
for /f "delims=" %%V in ('python _build_helpers.py current-version') do set CUR_VER=%%V

echo ============================================
echo   Claude Code Installer - Build Script
echo ============================================
echo.
echo   Current version: v%CUR_VER%
echo.
set /p "VERSION=Enter new version (e.g. 2.1, leave blank to keep current): "
echo.

:: Validate input (no spaces or special chars)
if not "!VERSION!"=="" (
    echo !VERSION!| findstr /r "^[0-9][0-9]*\.[0-9][0-9]*$" >nul
    if !errorlevel! neq 0 (
        echo [ERROR] Invalid version format. Use MAJOR.MINOR like 2.1
        pause
        exit /b 1
    )
)

:: Verify crypto keys exist
if not exist "..\app\keys\public.json" (
    echo [ERROR] app/keys/public.json not found.
    echo         Run: python server/crypto.py  to generate keys first.
    echo         WARNING: Regenerating keys will invalidate all existing clients.
    echo         Only regenerate if keys were never generated before.
    pause
    exit /b 1
)

:: Install dependencies
echo [1/3] Installing dependencies...
pip install -r ..\requirements.txt --quiet
if %errorlevel% neq 0 (
    echo [ERROR] Failed to install dependencies.
    pause
    exit /b 1
)

:: Clean previous build
echo [2/3] Cleaning previous build...
if exist "dist"   rmdir /s /q dist
if exist "build"  rmdir /s /q build

:: Generate config.ini in dist
echo [2.5] Generating config.ini...
if not exist "dist" mkdir dist
echo # config.ini > "dist\config.ini"
echo # mode = test >> "dist\config.ini"
echo # logging = 1 >> "dist\config.ini"
echo mode = test >> "dist\config.ini"
echo logging = 1 >> "dist\config.ini"

:: Update APP_VERSION if provided
if not "!VERSION!"=="" (
    echo [2.8] Updating APP_VERSION to !VERSION!...
    python _build_helpers.py set-version !VERSION!
    if !errorlevel! neq 0 (
        echo [ERROR] Failed to update APP_VERSION.
        pause
        exit /b 1
    )
    echo.
)

:: Build (CWD is scripts/, build.spec paths use ../ to reach project root)
echo [3/3] Building exe with PyInstaller...
python -m PyInstaller build.spec --noconfirm
if %errorlevel% neq 0 (
    echo [ERROR] Build failed.
    pause
    exit /b 1
)

:: Verify
echo.
echo [4/4] Verifying build...
if not exist "dist\ClaudeCodeInstaller.exe" (
    echo [ERROR] exe not found in dist\
    pause
    exit /b 1
)

for %%A in ("dist\ClaudeCodeInstaller.exe") do (
    echo   Output: dist\ClaudeCodeInstaller.exe  (%%~zA bytes)
)

echo.
echo ============================================
if not "!VERSION!"=="" (
    echo   Build complete!  v!VERSION!
    echo   Run: ./deploy.sh   to package and deploy
) else (
    echo   Build complete!
)
echo ============================================

echo.
pause
