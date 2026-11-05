#!/usr/bin/env bash
# ==============================================================================
# DeaCam Portal Lite 24/7 — Apagador para Raspberry Pi
# ==============================================================================

echo "=================================================================="
echo " 🛑 Deteniendo DeaCam Portal Lite 24/7 (Raspberry Pi)"
echo "=================================================================="

pkill -f "iniciar_portal_lite.sh" 2>/dev/null || true
pkill -f "portal_lite_24_7.py" 2>/dev/null || true
pkill -f "simulador_par_remoto.py" 2>/dev/null || true
pkill -f "unclutter" 2>/dev/null || true

echo "[OK] Portal Lite y guardianes detenidos completamente."
