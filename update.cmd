@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0" || (
  echo TrendRelay updater could not open the project folder.
  exit /b 1
)

title TrendRelay Project Updater
set "TRENDRELAY_NO_PAUSE="
if /i "%~1"=="--no-pause" set "TRENDRELAY_NO_PAUSE=1"

echo ==============================================================
echo                  TrendRelay Project Updater
echo ==============================================================
echo.

where git >nul 2>&1
if errorlevel 1 (
  echo Git is not installed or is not available in PATH.
  goto :failed
)

git rev-parse --is-inside-work-tree >nul 2>&1
if errorlevel 1 (
  echo This folder is not a Git working tree.
  goto :failed
)

set "TRENDRELAY_DIRTY="
for /f "delims=" %%I in ('git status --porcelain --untracked-files^=normal 2^>nul') do set "TRENDRELAY_DIRTY=1"
if defined TRENDRELAY_DIRTY (
  echo Update stopped because this project has local changes.
  echo Commit or stash them first so the updater cannot overwrite your work.
  echo.
  git status --short 2>nul
  goto :failed
)

set "TRENDRELAY_BRANCH="
for /f "delims=" %%I in ('git symbolic-ref --quiet --short HEAD 2^>nul') do set "TRENDRELAY_BRANCH=%%I"
if not defined TRENDRELAY_BRANCH (
  echo Update stopped because Git is in detached HEAD mode.
  echo Switch to a branch, then run update.cmd again.
  goto :failed
)

git rev-parse --abbrev-ref --symbolic-full-name "@{upstream}" >nul 2>&1
if errorlevel 1 (
  echo Branch "%TRENDRELAY_BRANCH%" has no upstream repository configured.
  echo Configure its Git remote and upstream, then run update.cmd again.
  goto :failed
)

set "TRENDRELAY_UPSTREAM="
for /f "delims=" %%I in ('git rev-parse --abbrev-ref --symbolic-full-name "@{upstream}"') do set "TRENDRELAY_UPSTREAM=%%I"
echo Branch:   %TRENDRELAY_BRANCH%
echo Tracking: %TRENDRELAY_UPSTREAM%
echo.
echo Pulling the latest safe update...

git pull --ff-only --prune
if errorlevel 1 (
  echo.
  echo TrendRelay was not updated. Git left the working tree unchanged.
  goto :failed
)

echo.
echo TrendRelay is up to date.
echo Run start.cmd for the browser app or start-electron.bat for the desktop app.
goto :success

:failed
set "TRENDRELAY_EXIT=1"
goto :finish

:success
set "TRENDRELAY_EXIT=0"

:finish
if not defined TRENDRELAY_NO_PAUSE pause
exit /b %TRENDRELAY_EXIT%
