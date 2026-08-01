@echo off
setlocal
cd /d "%~dp0"

if not exist ".data\tmp\" mkdir ".data\tmp"
set "TEMP=%CD%\.data\tmp"
set "TMP=%CD%\.data\tmp"

set "TRENDRELAY_DESKTOP_REQUESTED=0"
set "TRENDRELAY_CHECK_REQUESTED=0"
for %%A in (%*) do if /I "%%~A"=="--desktop" set "TRENDRELAY_DESKTOP_REQUESTED=1"
for %%A in (%*) do if /I "%%~A"=="--check" set "TRENDRELAY_CHECK_REQUESTED=1"

where node >nul 2>nul
if errorlevel 1 (
  echo TrendRelay requires Node.js 22 or newer.
  echo Download it from https://nodejs.org/
  pause
  exit /b 1
)

node -e "process.exit(Number(process.versions.node.split('.')[0]) >= 22 ? 0 : 1)"
if errorlevel 1 (
  echo TrendRelay requires Node.js 22 or newer.
  echo Update Node.js from https://nodejs.org/ and run start.cmd again.
  pause
  exit /b 1
)
where npm >nul 2>nul
if errorlevel 1 (
  echo npm was not found. Reinstall Node.js with npm enabled.
  pause
  exit /b 1
)

where python >nul 2>nul
if errorlevel 1 (
  echo TrendRelay requires Python 3.12 or newer.
  echo Download it from https://www.python.org/
  pause
  exit /b 1
)

python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)"
if errorlevel 1 (
  echo TrendRelay requires Python 3.12 or newer.
  echo Update Python from https://www.python.org/downloads/ and run start.cmd again.
  pause
  exit /b 1
)
if not exist "node_modules" (
  echo [Setup 1/4] Installing application dependencies...
  echo This is a one-time download and may take several minutes.
  call npm ci --no-audit --no-fund
  if errorlevel 1 goto :install_error
) else (
  echo [Setup 1/4] Application dependencies are ready.
)

if "%TRENDRELAY_DESKTOP_REQUESTED%"=="1" if not exist "node_modules\electron\dist\electron.exe" (
  echo Electron runtime is missing. Repairing the local installation...
  call node node_modules\electron\install.js
  if errorlevel 1 goto :install_error
)

if not exist ".venv\Scripts\python.exe" (
  echo [Setup 2/4] Creating the Python environment...
  python -m venv .venv
  if errorlevel 1 goto :install_error
) else (
  echo [Setup 2/4] Python environment is ready.
)

if "%TRENDRELAY_CHECK_REQUESTED%"=="0" (
  echo [Setup 3/4] Preparing API dependencies...
  ".venv\Scripts\python.exe" scripts\bootstrap.py
  if errorlevel 1 goto :install_error
)

if "%TRENDRELAY_CHECK_REQUESTED%"=="0" (
  echo [Setup 4/4] Applying database migrations...
  ".venv\Scripts\python.exe" scripts\db.py upgrade
  if errorlevel 1 goto :install_error
)

if "%TRENDRELAY_CHECK_REQUESTED%"=="0" if not exist ".data\postiz-selfhost\prepared-revision.txt" (
  echo Preparing native self-hosted Postiz for the first run...
  ".venv\Scripts\python.exe" scripts\postiz_service.py prepare
  if errorlevel 1 goto :install_error
)

if "%TRENDRELAY_CHECK_REQUESTED%"=="1" (
  ".venv\Scripts\python.exe" scripts\dev.py --check %*
  if errorlevel 1 exit /b 1
  exit /b 0
)

".venv\Scripts\python.exe" scripts\dev.py %*
if errorlevel 1 goto :run_error
exit /b 0

:install_error
echo TrendRelay dependency setup failed. Review the error above and try again.
echo If a download timed out, confirm GitHub and PyPI are reachable, then rerun start.cmd.
pause
exit /b 1

:run_error
echo TrendRelay stopped because of an error.
pause
exit /b 1
