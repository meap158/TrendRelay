@echo off
setlocal
cd /d "%~dp0"

echo Starting TrendRelay as an Electron development app...
call "%~dp0start.cmd" --desktop %*
exit /b %errorlevel%
