@echo off
title DeaCam Portal Lite LIGERO - Videollamada P2P (2 camaras)
cd /d "%~dp0"

echo ====================================================================
echo   DeaCam Portal Lite LIGERO (para equipos modestos, 2 camaras)
echo ====================================================================
echo Presione F12, Esc, Ctrl+A o Clic Derecho para abrir Ajustes.
echo.

:loop
echo [%date% %time%] Iniciando Portal Lite Ligero...
python portal_lite_24_7.py
echo [%date% %time%] El proceso se cerro. Reiniciando en 2 segundos...
ping 127.0.0.1 -n 3 >nul
goto loop
