@echo off
chcp 65001 >nul
title DeaCam Portal 24/7 - Auto-Arranque y Guardian Perpetuo
cd /d "%~dp0"

if "%1"=="--install" (
    echo Instalando acceso directo de Auto-Arranque 24/7 en el Inicio de Windows...
    powershell -NoProfile -Command "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut([Environment]::GetFolderPath('Startup') + '\DeaCam_Portal_24_7.lnk'); $s.TargetPath = '%~f0'; $s.WorkingDirectory = '%~dp0'; $s.WindowStyle = 7; $s.Save()"
    echo [OK] Auto-Arranque instalado en shell:startup. Si el equipo pierde energia y enciende, iniciara el Portal 24/7 automaticamente.
    goto :eof
)

:loop
echo [%date% %time%] Iniciando Portal Llamada 24/7 en Fullscreen sin bordes...
python portal_llamada_24_7.py --kiosk
timeout /t 2 /nobreak >nul
goto loop
