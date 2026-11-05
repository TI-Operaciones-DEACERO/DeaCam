@echo off
title Detener y Apagar DeaCam Portal Lite 24/7
cd /d "%~dp0"

echo ====================================================================
echo   DETENIENDO DEACAM PORTAL LITE 24/7 (WINDOWS)
echo ====================================================================
echo.

echo [1/2] Cerrando bucles guardianes activos...
taskkill /f /fi "WINDOWTITLE eq DeaCam Portal*" /im cmd.exe >nul 2>&1

echo [2/2] Finalizando procesos de Python (Portal Lite, Simulador, P2P, Audio)...
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -match 'python' -and ($_.CommandLine -match 'portal_lite_24_7' -or $_.CommandLine -match 'simulador_par_remoto') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"

echo.
echo ====================================================================
echo   PORTAL LITE 24/7 DETENIDO Y APAGADO CORRECTAMENTE.
echo ====================================================================
if not "%1"=="--silent" (
    pause
)
