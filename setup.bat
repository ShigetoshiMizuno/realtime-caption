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
echo.
echo  This will download approx. 2.5 GB in total.
echo  Please wait -- this may take 10-30 minutes.
echo ============================================
echo.

:: --- Python install ---
if exist "%PYTHON_DIR%\python.exe" (
    echo [SKIP] Python already installed.
) else (
    echo [INFO] Downloading Python %PYTHON_VER% (approx. 10 MB^)...
    curl.exe -L --progress-bar -o "%PYTHON_ZIP%" "%PYTHON_URL%" || goto error
    echo [INFO] Extracting Python...
    powershell -Command "Expand-Archive -Path '%PYTHON_ZIP%' -DestinationPath '%PYTHON_DIR%' -Force" || goto error
    del %PYTHON_ZIP%

    :: enable site-packages
    powershell -Command "(Get-Content '%PYTHON_DIR%\python311._pth') -replace '#import site','import site' | Set-Content '%PYTHON_DIR%\python311._pth'"
    echo [INFO] Python ready.

    :: install pip
    echo [INFO] Downloading pip...
    curl.exe -L --progress-bar -o get-pip.py https://bootstrap.pypa.io/get-pip.py || goto error
    echo [INFO] Installing pip...
    %PYTHON_DIR%\python.exe get-pip.py --quiet || goto error
    del get-pip.py
)

:: --- packages ---
echo.
echo [INFO] Installing packages (approx. 2 GB, may take several minutes^)...
%PYTHON_DIR%\python.exe -m pip install -r requirements.txt || goto error

:: --- models ---
echo.
echo [INFO] Downloading models (Whisper small approx. 500 MB^)...
%PYTHON_DIR%\python.exe download_models.py || goto error

echo.
echo ============================================
echo  Setup complete!
echo  Run: start.bat
echo ============================================
endlocal
exit /b 0

:error
echo.
echo [ERROR] Setup failed. Check your internet connection and run setup.bat again.
pause
exit /b 1
