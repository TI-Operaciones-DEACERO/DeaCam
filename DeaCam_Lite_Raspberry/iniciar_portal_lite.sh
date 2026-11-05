#!/usr/bin/env bash
# ==============================================================================
# DeaCam Portal Lite 24/7 — Lanzador Guardián para Raspberry Pi
# ==============================================================================

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORTAL_SCRIPT="$SCRIPT_DIR/portal_lite_24_7.py"

export DISPLAY="${DISPLAY:-:0}"
export XAUTHORITY="${XAUTHORITY:-$HOME/.Xauthority}"
export PYTHONUNBUFFERED=1

# Desactivar protector de pantalla y ahorro de energia en Raspberry Pi
xset s off 2>/dev/null || true
xset -dpms 2>/dev/null || true
xset s noblank 2>/dev/null || true

# Ocultar cursor del mouse tras 3s de inactividad
if command -v unclutter >/dev/null 2>&1; then
    killall unclutter 2>/dev/null || true
    unclutter -idle 3 -root &
fi

cd "$SCRIPT_DIR"

echo "=================================================================="
echo " 🟢 Iniciando DeaCam Portal Lite 24/7 en Raspberry Pi"
echo " Pulsa F12, Esc o Clic Derecho para abrir Ajustes"
echo "=================================================================="

while true; do
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Arrancando portal_lite_24_7.py..."
    python3 "$PORTAL_SCRIPT" || true
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] El proceso se detuvo. Reiniciando en 2 segundos..."
    sleep 2
done
