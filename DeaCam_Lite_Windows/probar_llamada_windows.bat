@echo off
title DeaCam Portal Lite 24/7 - Prueba de Videollamada 1 a 1 en Pantalla Completa
cd /d "%~dp0"

echo ====================================================================
echo   INICIANDO PRUEBA DE VIDEOLLAMADA 1 A 1 EN PANTALLA COMPLETA
echo ====================================================================
echo.
echo [1/2] Abriendo DeaCam Portal Lite 24/7 en Pantalla Completa...
start "DeaCam Portal Lite" python portal_lite_24_7.py

echo [2/2] Esperando inicializacion P2P (2 segundos)...
ping 127.0.0.1 -n 3 >nul

echo [3/3] Conectando Participante Remoto de prueba a la llamada...
start "Simulador Participante Remoto" python simulador_par_remoto.py 180 "Companero Remoto (Prueba 1 a 1)"

echo.
echo ====================================================================
echo   PRUEBA EN PANTALLA COMPLETA EN EJECUCION!
echo   * Veras la llamada 1 a 1 en vivo ocupando toda la pantalla.
echo   * Pulsa F12, Esc o Clic Derecho para abrir los Ajustes flotantes.
echo   * Pulsa F11 o Doble Clic para alternar entre pantalla completa y ventana.
echo   * Para cerrar la prueba, ejecuta apagar_portal_windows.bat
echo ====================================================================
