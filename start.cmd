@echo off
setlocal
cd /d "%~dp0"

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

if not exist "node_modules\" (
  echo Installing TrendRelay dependencies for the first run...
  call npm install
  if errorlevel 1 (
    echo Dependency installation failed. Review the error above and try again.
    pause
    exit /b 1
  )
)

if /I "%TRENDRELAY_START_CHECK%"=="1" (
  echo TrendRelay launcher checks passed.
  exit /b 0
)

echo Starting TrendRelay at http://localhost:3000
echo Keep this window open while using the application.
call npm run dev:web

if errorlevel 1 (
  echo TrendRelay stopped because of an error.
  pause
  exit /b 1
)

endlocal
