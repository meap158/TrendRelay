@echo off
setlocal
cd /d "%~dp0" || (
  echo Could not open the project folder.
  exit /b 1
)

title TrendRelay - Update and Start

echo ==============================================================
echo            TrendRelay - Update and Start
echo ==============================================================
echo.

call "%~dp0update.cmd" --no-pause
if errorlevel 1 (
  echo.
  echo Update failed. TrendRelay will not start.
  pause
  exit /b 1
)

echo.
echo Starting TrendRelay...
echo.

call "%~dp0start.cmd" %*
exit /b %errorlevel%
