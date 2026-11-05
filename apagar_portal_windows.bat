@echo off
chcp 65001 >nul
title Detener y Apagar DeaCam Portal 24/7
cd /d "%~dp0"

echo ======================================================================
echo   🛑 DETENIENDO DEACAM PORTAL 24/7 (WINDOWS)
echo ======================================================================
echo.

if "%1"=="--uninstall" (
    echo Desinstalando auto-arranque de Windows (shell:startup)...
    powershell -NoProfile -Command "Remove-Item -Path ([Environment]::GetFolderPath('Startup') + '\DeaCam_Portal_24_7.lnk') -Force -ErrorAction SilentlyContinue; Remove-Item -Path ([Environment]::GetFolderPath('Startup') + '\DeaCam_Portal_Lite_24_7.lnk') -Force -ErrorAction SilentlyContinue"
    echo [OK] Acceso directo eliminado del inicio de Windows.
    echo.
)

echo [1/3] Cerrando bucles guardianes activos (autostart_windows)...
taskkill /f /fi "WINDOWTITLE eq DeaCam Portal*" /im cmd.exe >nul 2>&1

echo [2/3] Finalizando procesos de Python de DeaCam (Portal, Lite, Panel, P2P, Audio)...
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -match 'python' -and ($_.CommandLine -match 'portal_llamada_24_7' -or $_.CommandLine -match 'portal_lite_24_7' -or $_.CommandLine -match 'visor_dahua') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"

echo [3/3] Liberando sockets y camaras...
timeout /t 1 /nobreak >nul

echo.
echo ======================================================================
echo   ✅ PORTAL 24/7 APAGADO CORRECTAMENTE
echo   (Se han cerrado todas las senales, camaras, audio y guardianes)
echo ======================================================================
echo.
if not "%1"=="--silent" (
    pause
)
