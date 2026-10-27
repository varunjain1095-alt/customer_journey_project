@echo off
REM ============================================================
REM  Customer Journey Analytics Dashboard — one-click launcher
REM  Double-click this file to start the backend + open the UI.
REM
REM  - Server binds http://127.0.0.1:8001 and serves the built UI
REM  - First launch warms analytics caches (~60s) — the page shows
REM    "Loading..." indicators until data is ready
REM  - The server holds the DuckDB file lock while running; close
REM    this window (or Ctrl+C) before regenerating the dataset
REM  - Requires frontend\dist to exist: cd frontend && npm run build
REM ============================================================

cd /d "%~dp0"

REM Open the browser ~5s after the server starts binding
start "" cmd /c "timeout /t 5 /nobreak >nul && start http://127.0.0.1:8001"

echo Starting dashboard at http://127.0.0.1:8001  (Ctrl+C to stop)
python -m uvicorn --factory src.server:create_app --host 127.0.0.1 --port 8001
