@echo off
title DeaCam Portal Lite 24/7 - Desinstalador de Auto-Arranque Windows
cd /d "%~dp0"

echo ====================================================================
echo   DESINSTALANDO AUTO-ARRANQUE 24/7 (WINDOWS)
echo ====================================================================
echo.
powershell -NoProfile -Command "Remove-Item -Path ([Environment]::GetFolderPath('Startup') + '\DeaCam_Portal_Lite_24_7.lnk') -Force -ErrorAction SilentlyContinue"

echo [OK] Acceso directo eliminado de Inicio de Windows (shell:startup).
echo El programa ya no iniciara solo al encender el equipo.
echo.
echo ====================================================================
echo   Desinstalacion completada.
echo ====================================================================
pause
