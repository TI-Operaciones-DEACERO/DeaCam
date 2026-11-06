@echo off
title DeaCam Portal Lite LIGERO - Quitar auto-arranque
powershell -NoProfile -Command "Remove-Item -Force -ErrorAction SilentlyContinue ([Environment]::GetFolderPath('Startup') + '\DeaCam_Portal_Ligero.lnk')"
echo [OK] Auto-arranque del portal ligero eliminado.
pause
