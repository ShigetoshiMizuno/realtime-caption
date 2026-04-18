@echo off
setlocal
cd /d "%~dp0"

set PYTHON_VER=3.11.9
set PYTHON_ZIP=python-%PYTHON_VER%-embed-amd64.zip
set PYTHON_URL=https://www.python.org/ftp/python/%PYTHON_VER%/%PYTHON_ZIP%
set PYTHON_DIR=python

echo ============================================
echo  realtime-caption
echo ============================================
echo.

:: --- config check ---
if not exist "config.yaml" (
    echo [ERROR] config.yaml not found.
    echo.
    echo   Copy config.yaml.example to config.yaml
    echo   and set your OpenAI API key.
    echo.
    echo   Example:
    echo     copy config.yaml.example config.yaml
    echo.
    pause
    exit /b 1
)

:: --- Python install (first time only) ---
if exist "%PYTHON_DIR%\python.exe" (
    echo [OK] Python ready.
) else (
    echo [SETUP] First-time setup. Downloading approx. 2.5 GB...
    echo        This may take 10-30 minutes. Please wait.
    echo.

    echo [1/4] Downloading Python %PYTHON_VER% (approx. 10 MB^)...
    curl.exe -L --progress-bar -o "%PYTHON_ZIP%" "%PYTHON_URL%" || goto error
    echo [1/4] Extracting Python...
    powershell -NoProfile -Command "Expand-Archive -Path '%PYTHON_ZIP%' -DestinationPath '%PYTHON_DIR%' -Force" || goto error
    del "%PYTHON_ZIP%"
    powershell -NoProfile -Command "(Get-Content '%PYTHON_DIR%\python311._pth') -replace '#import site','import site' | Set-Content '%PYTHON_DIR%\python311._pth'"

    echo [2/4] Installing pip...
    curl.exe -L --progress-bar -o get-pip.py https://bootstrap.pypa.io/get-pip.py || goto error
    %PYTHON_DIR%\python.exe get-pip.py --quiet || goto error
    del get-pip.py

    echo [3/4] Installing packages (approx. 2 GB^)...
    %PYTHON_DIR%\python.exe -m pip install -r requirements.txt || goto error

    echo [4/4] Downloading models (Whisper approx. 500 MB^)...
    %PYTHON_DIR%\python.exe download_models.py || goto error

    echo.
    echo [SETUP] Setup complete!
    echo.
)

:: --- launch ---
echo Starting...
echo.
%PYTHON_DIR%\python.exe main.py
if %ERRORLEVEL% neq 0 (
    echo.
    echo [ERROR] Exit code: %ERRORLEVEL%
    pause
)
endlocal
exit /b 0

:error
echo.
echo [ERROR] Setup failed. Check your internet connection and run start.bat again.
pause
exit /b 1
