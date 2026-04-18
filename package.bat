@echo off
setlocal

for /f "tokens=2 delims==" %%I in ('wmic os get localdatetime /value') do set DT=%%I
set TODAY=%DT:~0,8%
set ZIPNAME=realtime-caption-%TODAY%.zip
set OUTDIR=%~dp0..
set OUTPATH=%OUTDIR%\%ZIPNAME%
set TMPDIR=%~dp0_pkg_tmp

echo ===================================
echo  realtime-caption package builder
echo  Output: %OUTPATH%
echo ===================================

if exist "%OUTPATH%" del /f "%OUTPATH%"
if exist "%TMPDIR%" rmdir /s /q "%TMPDIR%"
mkdir "%TMPDIR%"

:: Copy files to temp dir (ensures CRLF for .bat files)
for %%F in (README.md config.yaml.example download_models.py main.py overlay.html requirements.txt setup.bat start.bat package.bat) do (
    copy /y "%~dp0%%F" "%TMPDIR%\%%F" >nul
)

:: Convert .bat files to CRLF
powershell -NoProfile -Command "Get-ChildItem '%TMPDIR%\*.bat' | ForEach-Object { $c = [System.IO.File]::ReadAllText($_.FullName); $c = $c -replace '(?<!\r)\n',\"`r`n\"; [System.IO.File]::WriteAllText($_.FullName, $c, [System.Text.Encoding]::Default) }"

:: Create zip from temp dir
powershell -NoProfile -Command "Compress-Archive -Path '%TMPDIR%\*' -DestinationPath '%OUTPATH%' -Force"

rmdir /s /q "%TMPDIR%"

if exist "%OUTPATH%" (
    echo [OK] %ZIPNAME% created.
    echo      Check that config.yaml is NOT included before distributing.
) else (
    echo [ERROR] Failed to create zip.
    pause
    exit /b 1
)
pause
