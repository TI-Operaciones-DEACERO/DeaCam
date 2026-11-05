@echo off
title DeaCam Portal Lite 24/7 - Videollamada P2P Mesh
cd /d "%~dp0"

if "%1"=="--install" (
    echo ====================================================================
    echo   Instalando Auto-Arranque 24/7 para DeaCam Portal Lite en Windows
    echo ====================================================================
    powershell -NoProfile -Command "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut([Environment]::GetFolderPath('Startup') + '\DeaCam_Portal_Lite_24_7.lnk'); $s.TargetPath = '%~f0'; $s.WorkingDirectory = '%~dp0'; $s.WindowStyle = 7; $s.Save()"
    echo [OK] Acceso directo creado en shell:startup.
    echo Si el equipo pierde energia y vuelve a encender, iniciara Portal Lite 24/7 automaticamente.
    goto :eof
)

echo ====================================================================
echo   DeaCam Portal Lite 24/7 (Espejo 1 a 1 y Grupal hasta 3 Participantes)
echo ====================================================================
echo Presione F12, Esc, Ctrl+A o Clic Derecho para abrir Ajustes de Audio/Camara.
echo.

:loop
echo [%date% %time%] Iniciando Portal Lite 24/7 en Pantalla Completa...
python portal_lite_24_7.py
echo [%date% %time%] El proceso se cerro. Reiniciando en 2 segundos para llamada 24/7 perpetua...
ping 127.0.0.1 -n 3 >nul
goto loop
