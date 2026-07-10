@echo off
setlocal
cd /d "%~dp0"
if exist "coach_system\STOP_REQUESTED" del /q "coach_system\STOP_REQUESTED"
python -m coach_system.supervisor %*
set "EXIT_CODE=%ERRORLEVEL%"
endlocal & exit /b %EXIT_CODE%
