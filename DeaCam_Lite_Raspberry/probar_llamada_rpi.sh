#!/usr/bin/env bash
# ==============================================================================
# DeaCam Portal Lite 24/7 — Prueba de Videollamada 1 a 1 en Raspberry Pi
# ==============================================================================

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

export DISPLAY="${DISPLAY:-:0}"
export PYTHONUNBUFFERED=1

echo "=================================================================="
echo " 🚀 INICIANDO PRUEBA DE VIDEOLLAMADA 1 A 1 EN RASPBERRY PI"
echo "=================================================================="

echo "[1/2] Abriendo DeaCam Portal Lite en Pantalla Completa..."
python3 "$SCRIPT_DIR/portal_lite_24_7.py" &
PORTAL_PID=$!

echo "[2/2] Esperando inicializacion P2P (3 segundos)..."
sleep 3

echo "[3/3] Conectando Participante Remoto simulado a la videollamada..."
python3 "$SCRIPT_DIR/simulador_par_remoto.py" 180 "Companero Remoto (Prueba RPi)" &
SIM_PID=$!

echo "=================================================================="
echo " 🟢 Prueba en ejecucion."
echo " Pulsa F12, Esc o Clic Derecho para abrir Ajustes."
echo " Para cerrar la prueba ejecuta: ./apagar_portal_rpi.sh"
echo "=================================================================="

wait $PORTAL_PID 2>/dev/null || true
kill $SIM_PID 2>/dev/null || true
