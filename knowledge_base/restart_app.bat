@echo off
setlocal

cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\windows\start_django_after_boot.ps1" %*
if errorlevel 1 (
    echo [ERROR] restart_app.bat: アプリ再起動に失敗しました。
    exit /b %errorlevel%
)

echo [OK] restart_app.bat: アプリを再起動しました。
exit /b 0
