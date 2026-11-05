#!/usr/bin/env bash
# ==============================================================================
# DeaCam Portal Lite 24/7 — Instalador de Auto-Arranque para Raspberry Pi
# ==============================================================================

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AUTOSTART_DIR="$HOME/.config/autostart"
DESKTOP_FILE="$AUTOSTART_DIR/deacam-portal-lite.desktop"

echo "=================================================================="
echo " 🚀 Configurando Auto-Arranque 24/7 de Portal Lite en Raspberry Pi"
echo "=================================================================="

echo "[1/3] Instalando dependencias del sistema operativo (Tkinter, PortAudio, X11)..."
if command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update -y || true
    sudo apt-get install -y python3-tk python3-pil python3-pil.imagetk libportaudio2 libatlas-base-dev x11-xserver-utils unclutter || true
fi

echo "[2/3] Instalando librerias de Python..."
python3 -m pip install -r "$SCRIPT_DIR/requirements.txt" --break-system-packages 2>/dev/null || \
python3 -m pip install -r "$SCRIPT_DIR/requirements.txt" || true

echo "[3/3] Creando demonio de auto-arranque en $DESKTOP_FILE..."
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
chmod +x "$SCRIPT_DIR"/*.sh

echo "=================================================================="
echo " [OK] Instalacion completada con exito."
echo " Si el Raspberry Pi se apaga o pierde energia electrica, al encender"
echo " abrira la videollamada 24/7 en Pantalla Completa y se unira solo."
echo "=================================================================="
