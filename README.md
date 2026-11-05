# DeaCam — Portal de Videollamada Perpetua 24/7 con Inteligencia Artificial

Sistema inteligente de videollamada perpetua 24/7 con soporte para cámaras IP Dahua PoE y Webcams USB, enlace P2P Mesh de baja latencia sin servidores intermedios, seguimiento cinemático de rostros con IA, zoom dinámico continuo y control gestual.

---

## 🚀 Ediciones Disponibles

### 1. DeaCam Lite 24/7 (Enfoque Espejo Puro 1 a 1 y Grupal hasta 3)
Diseñado específicamente para portales dedicados y pantallas continuas tipo kiosco:
- **Modo Espejo Limpio 1 a 1**: Pantalla completa sin bordes, menús ni barras de estado. Tú miras al otro participante y el otro te mira a ti.
- **Llamada Grupal Automática (hasta 3 participantes)**: Si un tercer nodo se conecta en la misma red local, la pantalla se divide automáticamente en vista cinematográfica de 2 paneles sin interrumpir la llamada.
- **Zoom Dinámico Suave IA**: Detección de contacto visual ultraligera (<2ms con YuNet ONNX) que acerca suavemente el encuadre (1.0x a 1.65x) mediante transformación afín sub-píxel continua sin saltos.
- **Oscilación Suave Relajante**: Paneo lento sinusoidal de izquierda a derecha cuando no hay personas frente a la cámara.
- **Audio Full-Duplex**: Micrófono y parlante activos con mezcla continua de todos los participantes.
- **Ajustes Flotantes**: Pulsa `F12`, `Esc`, `Ctrl+A` o haz `Clic Derecho` para abrir la ventana de configuración sin cortar la videollamada.

#### Carpetas Autónomas Listas para Compartir:
* **`DeaCam_Lite_Windows/`**: Paquete para Windows con instalador de inicio automático, desinstalador, apagador y tester (`probar_llamada_windows.bat`).
* **`DeaCam_Lite_Raspberry/`**: Paquete para Raspberry Pi OS con scripts de autoarranque ante cortes de energía eléctrica, desactivación de suspensión HDMI y dependencias del sistema (`libportaudio2`, `x11-xserver-utils`, `unclutter`).

---

### 2. DeaCam Full (Portal + Visor Dahua + Juegos Kinect + Gemini IA)
- **Visor Dahua Dual PoE**: Transmisión simultánea o alternada de 2 cámaras Dahua con subflujo RTSP optimizado.
- **Detección de Gestos & Juegos Kinect**: Detección de extremidades y manos con MediaPipe para jugar en llamada o en solitario, con gesto de pellizco (*pinch-to-click*).
- **Interpretación con Gemini IA**: Integración puntual con Google Gemini API para análisis de contexto y moderación visual.
- **Panel Moderno Glassmorphism**: Interfaz oscura translúcida con almacenamiento de preferencias en `config_portal.json`.

---

## 🛠️ Instalación y Requisitos

### Requisitos de Python:
- Python 3.10 o superior
- Dependencias:
  ```bash
  pip install -r requirements.txt
  ```

### En Windows:
1. Para probar la llamada en pantalla completa:
   ```cmd
   cd DeaCam_Lite_Windows
   probar_llamada_windows.bat
   ```
2. Para instalar el inicio automático tras cortes de energía:
   ```cmd
   instalar_autoarranque_windows.bat
   ```
3. Para apagar el portal:
   ```cmd
   apagar_portal_windows.bat
   ```

### En Raspberry Pi:
1. Dar permisos de ejecución:
   ```bash
   cd DeaCam_Lite_Raspberry
   chmod +x *.sh
   ```
2. Instalar dependencias del sistema y auto-arranque:
   ```bash
   ./instalar_autoarranque_rpi.sh
   ```
3. Probar la videollamada:
   ```bash
   ./probar_llamada_rpi.sh
   ```

---

## 🔒 Configuración de Red y Seguridad

El sistema utiliza puertos directos en la red de área local (LAN):
- **37990 UDP**: Descubrimiento y anuncio automático P2P (*Broadcast*).
- **37991 TCP**: Flujo de video continuo con compresión JPEG en tiempo real.
- **37992 UDP**: Flujo de audio PCM de baja latencia.

Copia `config_portal.example.json` a `config_portal.json` para personalizar contraseñas de cámaras RTSP, umbrales de zoom o dispositivos de sonido predeterminados.
