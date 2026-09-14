@echo off
setlocal EnableExtensions

title GridScout
if "%WSL_PROJECT_DIR%"=="" (
    for /f "usebackq delims=" %%i in (`wsl.exe wslpath -a -u "%~dp0" 2^>nul`) do set "WSL_PROJECT_DIR=%%i"
)
if "%WSL_PROJECT_DIR%"=="" set "WSL_PROJECT_DIR=."
set "COMPOSE_PROJECT_NAME=gridscout"

echo ===================================================
echo                 Starting GridScout
echo ===================================================
echo.
echo Project: %WSL_PROJECT_DIR%
echo Web:     http://localhost:3000
echo API:     http://localhost:8000
echo.

where wsl.exe >nul 2>&1
if errorlevel 1 (
    echo ERROR: wsl.exe was not found.
    echo Install or enable WSL before running GridScout.
    pause
    exit /b 1
)

echo Checking the Docker daemon in WSL...
wsl.exe -e bash -lc "docker info >/dev/null 2>&1"
if errorlevel 1 (
    echo.
    echo ERROR: Docker is not running in WSL.
    echo Start Docker in WSL and run this file again.
    pause
    exit /b 1
)

echo Preparing the WSL browser and application stack...
wsl.exe -e bash -lc "cd '%WSL_PROJECT_DIR%' && docker compose --profile container-fallback stop browser-worker >/dev/null 2>&1 || true"
if errorlevel 1 (
    echo.
    echo ERROR: Could not prepare the WSL browser worker.
    pause
    exit /b 1
)

wsl.exe -e bash -lc "cd '%WSL_PROJECT_DIR%' && if grep -Eq '^APP_MODE=live([[:space:]]|$)' .env 2>/dev/null; then command -v google-chrome >/dev/null 2>&1 || { echo 'google-chrome is required in WSL.'; exit 1; }; command -v setsid >/dev/null 2>&1 || { echo 'setsid is required in WSL.'; exit 1; }; test -x .venv/bin/python || { echo '.venv/bin/python is required in WSL.'; exit 1; }; mkdir -p .cache/olx_browser_profile .cache/olx_session; if [ -f .cache/gridscout-browser-worker.pid ]; then worker_pid=$(cat .cache/gridscout-browser-worker.pid 2>/dev/null || true); if ps -p $worker_pid -o args= 2>/dev/null | grep -Fq 'apps.browser_worker.main'; then kill $worker_pid >/dev/null 2>&1 || true; for i in $(seq 1 20); do kill -0 $worker_pid >/dev/null 2>&1 || break; sleep 0.2; done; fi; fi; if ! curl -fsS http://127.0.0.1:8100/health >/dev/null 2>&1; then setsid -f sh -c 'echo $$ > .cache/gridscout-browser-worker.pid; exec env OLX_CDP_ENDPOINT=http://127.0.0.1:9222 OLX_CHROME_BINARY=google-chrome OLX_CHROME_PROFILE_DIR=%WSL_PROJECT_DIR%/.cache/olx_browser_profile OLX_CHROME_LOG_FILE=%WSL_PROJECT_DIR%/.cache/gridscout-olx-chrome.log OLX_SESSION_DIR=%WSL_PROJECT_DIR%/.cache/olx_session BROWSER_WORKER_PORT=8100 OLX_LOGIN_HEADLESS=false .venv/bin/python -m apps.browser_worker.main' >.cache/gridscout-browser-worker.log 2>&1; fi; for i in $(seq 1 30); do curl -fsS http://127.0.0.1:8100/health >/dev/null 2>&1 && break; sleep 1; done; curl -fsS http://127.0.0.1:8100/health >/dev/null 2>&1 || { echo 'WSL browser worker did not start.'; exit 1; }; fi"
if errorlevel 1 (
    echo.
    echo ERROR: The WSL browser worker could not start.
    echo Check .cache/gridscout-olx-chrome.log and .cache/gridscout-browser-worker.log in WSL.
    pause
    exit /b 1
)

echo Building and starting the application stack...
wsl.exe -e bash -lc "cd '%WSL_PROJECT_DIR%' && export COMPOSE_PROJECT_NAME='%COMPOSE_PROJECT_NAME%' && docker compose up --build -d"
if errorlevel 1 (
    echo.
    echo ERROR: Docker Compose could not start GridScout.
    wsl.exe -e bash -lc "cd '%WSL_PROJECT_DIR%' && docker compose ps"
    pause
    exit /b 1
)

echo Waiting for the API and database to become ready...
wsl.exe -e bash -lc "for i in $(seq 1 60); do if curl -fsS http://localhost:8000/api/v1/health >/dev/null 2>&1; then exit 0; fi; sleep 2; done; exit 1"
if errorlevel 1 (
    echo.
    echo ERROR: The API did not become healthy within 120 seconds.
    wsl.exe -e bash -lc "cd '%WSL_PROJECT_DIR%' && docker compose ps && docker compose logs --tail=80 api db-init worker"
    pause
    exit /b 1
)

echo.
echo GridScout is ready. Opening the application and live logs terminal...
start "" "http://localhost:3000"
start "GridScout - Logs" wsl.exe -e bash -lc "cd '%WSL_PROJECT_DIR%' && export COMPOSE_PROJECT_NAME='%COMPOSE_PROJECT_NAME%' && (.venv/bin/python scripts/live_logs.py || python3 scripts/live_logs.py)"
echo.
echo Live logs terminal opened in the background.
echo.
echo To stop the stack later, run:
echo   wsl.exe -e bash -lc "cd '%WSL_PROJECT_DIR%' && docker compose down"
echo.
pause
