#!/usr/bin/env bash
# ==============================================================================
# DeaCam Portal Lite 24/7 — Desinstalador de Auto-Arranque para Raspberry Pi
# ==============================================================================

echo "=================================================================="
echo " 🛑 Desinstalando Auto-Arranque 24/7 de Raspberry Pi"
echo "=================================================================="

rm -f "$HOME/.config/autostart/deacam-portal-lite.desktop"
echo "[OK] Auto-arranque removido de ~/.config/autostart."
