#!/usr/bin/env bash
# ==============================================================================
# DeaCam Portal 24/7 — Instalador y Demonio de Auto-Arranque para Raspberry Pi
# ==============================================================================
# Función:
#   1. Si el Raspberry Pi pierde energía eléctrica y vuelve a encender solo,
#      inicia automáticamente el entorno gráfico sin protector de pantalla,
#      ejecuta `portal_llamada_24_7.py` en Fullscreen SIN BORDES y se auto-une
#      a la llamada perpetua 24/7 en la red P2P.
#   2. Si el administrador conecta un teclado o mouse al Raspberry Pi para
#      ajustar algo, solo debe pulsar F12, Esc, Ctrl+A o hacer Clic Derecho
#      para abrir el Panel de Administrador encima de la pantalla.
#
# Uso en el Raspberry Pi:
#   chmod +x autostart_raspberry.sh
#   ./autostart_raspberry.sh --install
# ==============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORTAL_SCRIPT="$SCRIPT_DIR/portal_llamada_24_7.py"
WATCHDOG_SCRIPT="$SCRIPT_DIR/run_portal_watchdog_rpi.sh"
AUTOSTART_DIR="$HOME/.config/autostart"
DESKTOP_FILE="$AUTOSTART_DIR/deacam-portal-24-7.desktop"

install_autostart() {
    echo "=================================================================="
    echo " 🚀 Configurando Auto-Arranque 24/7 en Raspberry Pi para DeaCam"
    echo "=================================================================="

    echo "[1/4] Verificando dependencias de sistema (Tkinter, PortAudio, X11)..."
    if command -v apt-get >/dev/null 2>&1; then
        sudo apt-get update -y || true
        sudo apt-get install -y python3-tk python3-pil python3-pil.imagetk libportaudio2 libatlas-base-dev x11-xserver-utils unclutter || true
    fi

    echo "[2/4] Verificando librerías de Python..."
    if [ -f "$SCRIPT_DIR/requirements.txt" ]; then
        python3 -m pip install -r "$SCRIPT_DIR/requirements.txt" --break-system-packages 2>/dev/null || \
        python3 -m pip install -r "$SCRIPT_DIR/requirements.txt" || true
    else
        python3 -m pip install opencv-python numpy pillow requests sounddevice --break-system-packages 2>/dev/null || \
        python3 -m pip install opencv-python numpy pillow requests sounddevice || true
    fi

    echo "[3/4] Creando Guardián (Watchdog 24/7 ante cortes de energía)..."
    cat > "$WATCHDOG_SCRIPT" << 'EOF'
#!/usr/bin/env bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

export DISPLAY="${DISPLAY:-:0}"
export XAUTHORITY="${XAUTHORITY:-$HOME/.Xauthority}"
export PYTHONUNBUFFERED=1

# Desactivar apagado de pantalla y ahorro de energía HDMI en Raspberry Pi
xset s off 2>/dev/null || true
xset -dpms 2>/dev/null || true
xset s noblank 2>/dev/null || true

# Ocultar cursor inactivo en X11 si unclutter está disponible
if command -v unclutter >/dev/null 2>&1; then
    unclutter -idle 2.5 -root &
fi

# Esperar 3 segundos a que la interfaz de red Ethernet/Wi-Fi obtenga IP tras corte eléctrico
sleep 3

# Bucle Perpetuo 24/7: si el proceso se cierra por cualquier motivo, revive en 2 segundos
while true; do
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Iniciando Portal Llamada Perpetua 24/7 en Fullscreen sin bordes..."
    python3 "$SCRIPT_DIR/portal_llamada_24_7.py" --kiosk
    EXIT_CODE=$?
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Proceso terminó con código $EXIT_CODE. Reiniciando en 2 segundos..."
    sleep 2
done
EOF
    chmod +x "$WATCHDOG_SCRIPT"

    echo "[4/4] Registrando Auto-Arranque en el inicio automático del escritorio Raspberry Pi..."
    mkdir -p "$AUTOSTART_DIR"
    cat > "$DESKTOP_FILE" << EOF
[Desktop Entry]
Type=Application
Name=DeaCam Portal Llamada 24/7
Comment=Portal de Llamada Perpetua 24/7 en Fullscreen Sin Bordes con Auto-Reconexión P2P
Exec=$WATCHDOG_SCRIPT
Path=$SCRIPT_DIR
Terminal=false
StartupNotify=false
X-GNOME-Autostart-enabled=true
EOF
    chmod +x "$DESKTOP_FILE"

    echo ""
    echo "✅ ¡Auto-Arranque 24/7 instalado exitosamente en el Raspberry Pi!"
    echo "   • Archivo Autostart: $DESKTOP_FILE"
    echo "   • Guardián Watchdog: $WATCHDOG_SCRIPT"
    echo "   • Si el Raspberry Pi pierde energía y vuelve a encender, entrará solo"
    echo "     a la llamada 24/7 en Fullscreen sin bordes."
    echo "   • Para abrir el Panel Admin con periféricos: pulsa F12, Esc, Ctrl+A o Clic Derecho."
}

if [[ "${1:-}" == "--install" ]]; then
    install_autostart
else
    echo "Uso:"
    echo "  ./autostart_raspberry.sh --install    (Instala dependencias y configura auto-inicio)"
    echo "  python3 portal_llamada_24_7.py        (Ejecuta llamada 24/7 directamente)"
    echo "  python3 visor_dahua.py                (Ejecuta panel de administración)"
fi
