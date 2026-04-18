@echo off
setlocal

cd /d "%~dp0"

echo ============================================
echo  realtime-caption build script
echo ============================================

pyinstaller --version >nul 2>&1
if errorlevel 1 (
    echo [INFO] pyinstaller not found. Installing...
    pip install pyinstaller
    if errorlevel 1 (
        echo [ERROR] Failed to install pyinstaller.
        pause
        exit /b 1
    )
)

echo [INFO] Running PyInstaller...
pyinstaller realtime-caption.spec --clean
if errorlevel 1 (
    echo [ERROR] Build failed.
    pause
    exit /b 1
)

echo.
echo ============================================
echo  Build succeeded!
echo ============================================
echo.
echo IMPORTANT: config.yaml is NOT included in the build.
echo Please copy config.yaml manually to the output folder:
echo.
echo   dist\realtime-caption\config.yaml
echo.
echo Then run: dist\realtime-caption\realtime-caption.exe
echo ============================================

endlocal
