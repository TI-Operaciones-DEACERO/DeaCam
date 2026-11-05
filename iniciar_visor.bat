@echo off
title DeaCam - Visor Portable Dahua
cd /d "%~dp0"
python visor_dahua.py
if errorlevel 1 pause
