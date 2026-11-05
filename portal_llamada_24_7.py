#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Portal de Llamada Perpetua 24/7 (Modo Kiosco Independiente Fullscreen Sin Bordes)
---------------------------------------------------------------------------------
- Se ejecuta directamente en Pantalla Completa SIN bordes, sin barra de navegación
  y con ocultamiento automático del puntero del mouse.
- Si el equipo (Raspberry Pi o PC) pierde energía y vuelve a encender, carga
  automáticamente `config_portal.json`, inicia la cámara universal + parlante
  y se auto-une a la llamada perpetua 24/7 en la red P2P.
- Si el Administrador conecta periféricos (teclado o mouse) y necesita ajustar algo:
  * Pulsar F12, Esc, Ctrl+A o hacer Clic Derecho abre de inmediato el Panel de Administrador.
  * Desde el Panel de Administrador puede volver al modo Solo Portal Fullscreen con 1 clic.
"""

import os
import sys
import tkinter as tk

from visor_dahua import DahuaPortalViewerApp


def main():
    # Asegurar directorio de trabajo en la carpeta del script (clave para auto-arranque en Raspberry Pi / systemd)
    base_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(base_dir)

    root = tk.Tk()
    # Iniciar en Modo Kiosco Independiente (oculta el Panel Admin al arrancar y abre el Portal 24/7 en Fullscreen sin bordes)
    app = DahuaPortalViewerApp(root, kiosk_mode=True)
    # Garantizar que en el arranque independiente siempre esté activo el auto-unir a la llamada 24/7
    app.auto_join_call_var.set(True)
    app.auto_open_portal_win_var.set(True)
    root.mainloop()


if __name__ == "__main__":
    main()
