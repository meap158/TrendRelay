@echo off
setlocal
cd /d "%~dp0"

where node >nul 2>nul
if errorlevel 1 (
  echo TrendRelay Postiz tooling requires Node.js 22 or newer.
  exit /b 1
)

where python >nul 2>nul
if errorlevel 1 (
  echo TrendRelay Postiz tooling requires Python 3.12 or newer.
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Creating the TrendRelay Python environment...
  python -m venv .venv
  if errorlevel 1 exit /b 1
)

".venv\Scripts\python.exe" scripts\postiz.py %*
exit /b %errorlevel%
