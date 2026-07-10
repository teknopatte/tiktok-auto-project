@echo off
setlocal
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0"
if exist "coach_system\STOP_REQUESTED" del /q "coach_system\STOP_REQUESTED"
python -m coach_system.supervisor %*
set "EXIT_CODE=%ERRORLEVEL%"
endlocal & exit /b %EXIT_CODE%
