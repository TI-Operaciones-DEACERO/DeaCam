#!/usr/bin/env bash
# ==============================================================================
# DeaCam Portal Lite 24/7 — Guardián e Instalador de Auto-Arranque para Raspberry Pi
# ==============================================================================
# Características:
# 1. Modo Espejo Limpio 1 a 1 y Grupal hasta 3 participantes en pantalla completa.
# 2. Si el Raspberry Pi pierde energía y vuelve a encender, inicia y conecta solo.
# 3. Pulsa F12, Esc o Clic Derecho para abrir el panel de configuración de audio/cámara.
#
# Uso:
#   chmod +x iniciar_portal_lite.sh
#   ./iniciar_portal_lite.sh           # Iniciar inmediatamente
#   ./iniciar_portal_lite.sh --install # Instalar auto-arranque persistente tras cortes de luz
# ==============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORTAL_SCRIPT="$SCRIPT_DIR/portal_lite_24_7.py"
AUTOSTART_DIR="$HOME/.config/autostart"
DESKTOP_FILE="$AUTOSTART_DIR/deacam-portal-lite.desktop"

if [ "$1" == "--install" ]; then
    echo "=================================================================="
    echo " 🚀 Configurando Auto-Arranque 24/7 de Portal Lite en Raspberry Pi"
    echo "=================================================================="

    echo "[1/3] Verificando dependencias del sistema..."
    if command -v apt-get >/dev/null 2>&1; then
        sudo apt-get update -y || true
        sudo apt-get install -y python3-tk python3-pil python3-pil.imagetk libportaudio2 libatlas-base-dev x11-xserver-utils unclutter || true
    fi

    echo "[2/3] Verificando dependencias de Python..."
    python3 -m pip install opencv-python numpy pillow sounddevice --break-system-packages 2>/dev/null || \
    python3 -m pip install opencv-python numpy pillow sounddevice || true

    echo "[3/3] Registrando demonio en $DESKTOP_FILE..."
    mkdir -p "$AUTOSTART_DIR"

    cat > "$DESKTOP_FILE" << EOF
[Desktop Entry]
Type=Application
Name=DeaCam Portal Lite 24-7
Comment=Portal Videollamada P2P Mesh 24/7 Auto-Arranque
Exec=$SCRIPT_DIR/iniciar_portal_lite.sh
Terminal=false
StartupNotify=false
X-GNOME-Autostart-enabled=true
EOF
    chmod +x "$DESKTOP_FILE"
    chmod +x "$SCRIPT_DIR/iniciar_portal_lite.sh"

    echo "=================================================================="
    echo " [OK] Instalación completada con éxito."
    echo " Ante cortes de energía eléctrica, el Raspberry Pi arrancará solo"
    echo " y se enlazará automáticamente a la videollamada 24/7."
    echo "=================================================================="
    exit 0
fi

# Desactivar protector de pantalla y suspensión HDMI
export DISPLAY="${DISPLAY:-:0}"
export XAUTHORITY="${XAUTHORITY:-$HOME/.Xauthority}"
export PYTHONUNBUFFERED=1

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
echo " 🟢 Iniciando Guardián DeaCam Portal Lite 24/7..."
echo " Presione F12, Esc, Ctrl+A o Clic Derecho para abrir Ajustes"
echo "=================================================================="

while true; do
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Arrancando portal_lite_24_7.py..."
    python3 "$PORTAL_SCRIPT" || true
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] El proceso se detuvo. Reiniciando en 2 segundos para servicio 24/7..."
    sleep 2
done
