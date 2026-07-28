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

if not exist "node_modules\" (
  echo Installing application dependencies for the first run...
  call npm install
  if errorlevel 1 goto :install_error
)

if "%TRENDRELAY_DESKTOP_REQUESTED%"=="1" if not exist "node_modules\electron\dist\electron.exe" (
  echo Electron runtime is missing. Repairing the local installation...
  call node node_modules\electron\install.js
  if errorlevel 1 goto :install_error
)

if not exist ".venv\Scripts\python.exe" (
  echo Creating the Python environment...
  python -m venv .venv
  if errorlevel 1 goto :install_error
)

if "%TRENDRELAY_CHECK_REQUESTED%"=="0" ".venv\Scripts\python.exe" -c "import trendrelay_api" >nul 2>nul
if "%TRENDRELAY_CHECK_REQUESTED%"=="0" if errorlevel 1 (
  echo Checking API dependencies...
  ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check --quiet -e "services/api[dev]"
  if errorlevel 1 goto :install_error
)

if "%TRENDRELAY_CHECK_REQUESTED%"=="0" (
  echo Applying database migrations...
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
pause
exit /b 1

:run_error
echo TrendRelay stopped because of an error.
pause
exit /b 1
