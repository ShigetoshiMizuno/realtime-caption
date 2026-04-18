@echo off
setlocal

:: 出力ファイル名（日付付き）
for /f "tokens=2 delims==" %%I in ('wmic os get localdatetime /value') do set DT=%%I
set TODAY=%DT:~0,8%
set ZIPNAME=realtime-caption-%TODAY%.zip

:: 出力先（このフォルダの一つ上）
set OUTDIR=%~dp0..
set OUTPATH=%OUTDIR%\%ZIPNAME%

echo ===================================
echo  realtime-caption 配布パッケージ作成
echo  出力: %OUTPATH%
echo ===================================

:: 既存の zip を削除
if exist "%OUTPATH%" del /f "%OUTPATH%"

:: PowerShell で zip 作成（配布対象ファイルのみ）
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$src = '%~dp0'; $dst = '%OUTPATH%'; " ^
  "$files = @(" ^
  "  'README.md'," ^
  "  'config.yaml.example'," ^
  "  'download_models.py'," ^
  "  'main.py'," ^
  "  'overlay.html'," ^
  "  'requirements.txt'," ^
  "  'setup.bat'," ^
  "  'start.bat'" ^
  "); " ^
  "$compress = $files | ForEach-Object { Join-Path $src $_ }; " ^
  "Compress-Archive -Path $compress -DestinationPath $dst -Force; " ^
  "Write-Host 'Done:' $dst"

if %ERRORLEVEL% neq 0 (
  echo [ERROR] zip 作成に失敗しました。
  pause
  exit /b 1
)

echo.
echo [OK] %ZIPNAME% を作成しました。
echo      配布前に config.yaml が含まれていないことを確認してください。
pause
