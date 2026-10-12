@echo off
rem Opens the local XIC workbench. Drop a result folder or Targeted XLSX on
rem this file to open it for review directly.
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -m xic_workbench %*
) else (
    uv run python -m xic_workbench %*
)
if errorlevel 1 pause
