@echo off
setlocal EnableExtensions DisableDelayedExpansion
set "PYTHONHOME="
set "PYTHONPATH="
set "PYTHONUTF8=1"
set "VTUBER_SETUP_ROOT=%~dp0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup.ps1" %*
set "SETUP_EXIT=%ERRORLEVEL%"
if not "%SETUP_EXIT%"=="0" echo Setup stopped. See logs\setup.log for details.
if not defined VTUBER_SETUP_NO_PAUSE pause
exit /b %SETUP_EXIT%
