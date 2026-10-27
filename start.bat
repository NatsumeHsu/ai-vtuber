@echo off
setlocal EnableExtensions DisableDelayedExpansion
set "PYTHONHOME="
set "PYTHONPATH="
start "" /D "%~dp0" "%~dp0.venv\Scripts\pythonw.exe" -X utf8 "%~dp0start_vtuber.pyw"
exit /b
