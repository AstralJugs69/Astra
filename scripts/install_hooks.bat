@echo off
REM scripts/install_hooks.bat — Installs Astra hooks into an Antigravity workspace (Windows)

set TARGET_WORKSPACE=%1
if "%TARGET_WORKSPACE%"=="" set TARGET_WORKSPACE=.

set SCRIPT_DIR=%~dp0
set HOOKS_DIR=%SCRIPT_DIR%..\hooks
set TARGET_DIR=%TARGET_WORKSPACE%\.agents

if not exist "%TARGET_DIR%" mkdir "%TARGET_DIR%"

echo {> "%TARGET_DIR%\hooks.json"
echo   "hooks": {>> "%TARGET_DIR%\hooks.json"
echo     "PostToolUse": {>> "%TARGET_DIR%\hooks.json"
echo       "command": "python",>> "%TARGET_DIR%\hooks.json"
echo       "args": ["%HOOKS_DIR:\=/%/post_tool_use.py"]>> "%TARGET_DIR%\hooks.json"
echo     },>> "%TARGET_DIR%\hooks.json"
echo     "Stop": {>> "%TARGET_DIR%\hooks.json"
echo       "command": "python",>> "%TARGET_DIR%\hooks.json"
echo       "args": ["%HOOKS_DIR:\=/%/stop.py"]>> "%TARGET_DIR%\hooks.json"
echo     }>> "%TARGET_DIR%\hooks.json"
echo   }>> "%TARGET_DIR%\hooks.json"
echo }>> "%TARGET_DIR%\hooks.json"

echo [OK] Astra hooks installed to %TARGET_DIR%\hooks.json
