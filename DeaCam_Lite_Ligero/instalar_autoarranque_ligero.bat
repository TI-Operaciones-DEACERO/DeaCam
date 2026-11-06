@echo off
title DeaCam Portal Lite LIGERO - Instalar auto-arranque
cd /d "%~dp0"

echo ====================================================================
echo   INSTALANDO AUTO-ARRANQUE DE DEACAM PORTAL LITE LIGERO (WINDOWS)
echo ====================================================================
echo.
powershell -NoProfile -Command "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut([Environment]::GetFolderPath('Startup') + '\DeaCam_Portal_Ligero.lnk'); $s.TargetPath = '%~dp0iniciar_portal_ligero.bat'; $s.WorkingDirectory = '%~dp0'; $s.WindowStyle = 7; $s.Save()"

echo [OK] Acceso directo registrado en Inicio de Windows (shell:startup).
echo Al encender el equipo se abrira el portal ligero en pantalla completa.
echo.
pause
