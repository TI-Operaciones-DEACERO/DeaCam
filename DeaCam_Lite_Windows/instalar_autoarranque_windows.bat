@echo off
title DeaCam Portal Lite 24/7 - Instalador de Auto-Arranque Windows
cd /d "%~dp0"

echo ====================================================================
echo   INSTALANDO AUTO-ARRANQUE 24/7 PARA DEACAM PORTAL LITE EN WINDOWS
echo ====================================================================
echo.
powershell -NoProfile -Command "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut([Environment]::GetFolderPath('Startup') + '\DeaCam_Portal_Lite_24_7.lnk'); $s.TargetPath = '%~dp0iniciar_portal_lite.bat'; $s.WorkingDirectory = '%~dp0'; $s.WindowStyle = 7; $s.Save()"

echo [OK] Acceso directo registrado en Inicio de Windows (shell:startup).
echo.
echo Si este equipo pierde energia y vuelve a encender, abrira la videollamada
echo 24/7 en Pantalla Completa y se enlazara solo a la red P2P.
echo.
echo ====================================================================
echo   Instalacion completada con exito.
echo ====================================================================
pause
