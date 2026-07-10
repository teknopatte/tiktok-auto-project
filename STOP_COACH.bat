@echo off
setlocal
cd /d "%~dp0"
if not exist "coach_system" (
  echo coach_system directory not found.
  exit /b 1
)
type nul > "coach_system\STOP_REQUESTED"
echo Graceful stop requested.
endlocal
