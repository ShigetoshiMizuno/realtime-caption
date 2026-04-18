@echo off
cd /d "%~dp0"

python\python.exe main.py
if %ERRORLEVEL% neq 0 (
    echo ERROR: exit code %ERRORLEVEL%
    pause
)
