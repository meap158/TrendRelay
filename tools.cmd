@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo TrendRelay environment missing. Run start.cmd first.
  exit /b 1
)

".venv\Scripts\python.exe" scripts\tools.py %*
exit /b %errorlevel%
