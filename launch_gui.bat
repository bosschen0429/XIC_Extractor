@echo off
rem Opens the local XIC workbench. Drop a result folder or Targeted XLSX on
rem this file to open it for review directly.
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -m xic_workbench %*
) else (
    uv run python -m xic_workbench %*
)
set "xic_exit_code=%errorlevel%"
if not defined XIC_BACKGROUND_LAUNCH if not "%xic_exit_code%"=="0" pause
exit /b %xic_exit_code%
