@echo off
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo TrendRelay Douyin tooling requires Python 3.12 or newer.
  echo Download it from https://www.python.org/
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Creating the TrendRelay Python environment...
  python -m venv .venv
  if errorlevel 1 exit /b 1
)

".venv\Scripts\python.exe" scripts\douyin.py %*
exit /b %errorlevel%
