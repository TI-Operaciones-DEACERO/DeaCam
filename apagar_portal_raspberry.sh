#!/usr/bin/env bash
# ==============================================================================
# DeaCam Portal 24/7 — Apagador y Desinstalador para Raspberry Pi / Linux
# ==============================================================================
# Uso:
#   ./apagar_portal_raspberry.sh              # Detiene el portal y el guardián
#   ./apagar_portal_raspberry.sh --uninstall  # Detiene y quita del auto-arranque
# ==============================================================================

echo "=================================================================="
echo " 🛑 Deteniendo DeaCam Portal 24/7 (Raspberry Pi)"
echo "=================================================================="

# 1. Si se pasa --uninstall, remover archivo desktop de autostart
if [[ "${1:-}" == "--uninstall" ]]; then
    echo "[*] Desinstalando del inicio automático (~/.config/autostart)..."
    rm -f "$HOME/.config/autostart/deacam-portal-24-7.desktop"
    rm -f "$HOME/.config/autostart/deacam-portal-lite.desktop"
    echo "    [OK] Auto-arranque removido."
fi

# 2. Matar el proceso Watchdog (Guardián para que no lo vuelva a revivir)
echo "[1/3] Deteniendo bucle Guardián Watchdog..."
pkill -f "run_portal_watchdog_rpi.sh" 2>/dev/null || true
pkill -f "iniciar_portal_lite.sh" 2>/dev/null || true

# 3. Matar procesos Python del Portal y Visor Dahua
echo "[2/3] Finalizando procesos de Python (Portal, Lite, P2P, Audio)..."
pkill -f "portal_llamada_24_7.py" 2>/dev/null || true
pkill -f "portal_lite_24_7.py" 2>/dev/null || true
pkill -f "visor_dahua.py" 2>/dev/null || true

# 4. Restaurar cursor si estaba unclutter corriendo
echo "[3/3] Restaurando puntero de mouse y liberando puertos..."
pkill -f "unclutter" 2>/dev/null || true

sleep 1
echo ""
echo "✅ ¡Portal 24/7 detenido y apagado completamente!"
