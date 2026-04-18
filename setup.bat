@echo off
setlocal
cd /d "%~dp0"

set PYTHON_VER=3.11.9
set PYTHON_ZIP=python-%PYTHON_VER%-embed-amd64.zip
set PYTHON_URL=https://www.python.org/ftp/python/%PYTHON_VER%/%PYTHON_ZIP%
set PYTHON_DIR=python

echo ============================================
echo  realtime-caption setup
echo ============================================

:: --- Python install ---
if exist "%PYTHON_DIR%\python.exe" (
    echo [SKIP] Python already installed.
) else (
    echo [INFO] Downloading Python %PYTHON_VER%...
    powershell -Command "Invoke-WebRequest -Uri '%PYTHON_URL%' -OutFile '%PYTHON_ZIP%'" || goto error
    powershell -Command "Expand-Archive -Path '%PYTHON_ZIP%' -DestinationPath '%PYTHON_DIR%'" || goto error
    del %PYTHON_ZIP%

    :: enable site-packages (python311._pth の #import site を uncomment)
    powershell -Command "(Get-Content '%PYTHON_DIR%\python311._pth') -replace '#import site','import site' | Set-Content '%PYTHON_DIR%\python311._pth'"

    :: install pip
    powershell -Command "Invoke-WebRequest -Uri 'https://bootstrap.pypa.io/get-pip.py' -OutFile 'get-pip.py'" || goto error
    %PYTHON_DIR%\python.exe get-pip.py || goto error
    del get-pip.py
)

:: --- packages ---
echo [INFO] Installing packages...
%PYTHON_DIR%\python.exe -m pip install -r requirements.txt || goto error

:: --- models ---
echo [INFO] Downloading models...
%PYTHON_DIR%\python.exe download_models.py || goto error

echo.
echo ============================================
echo  Setup complete!
echo  Run: start.bat
echo ============================================
endlocal
exit /b 0

:error
echo [ERROR] Setup failed.
pause
exit /b 1
