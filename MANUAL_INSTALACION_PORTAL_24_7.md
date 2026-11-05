# 🌐 MANUAL DE INSTALACIÓN Y PUESTA EN MARCHA
## DeaCam Portal 24/7 — Sistema de Llamada Perpetua LAN P2P, IA Biometría & Moderación, Juegos Kinect AR

---

## 📋 1. Descripción del Sistema
**DeaCam Portal 24/7** es una estación de videollamada perpetua inspirada en instalaciones públicas como *"The Portal"* (NY–Dublín). Conecta dos o más nodos de forma autónoma en red de área local (LAN) mediante arquitectura **P2P Zero-Lag**, sin depender de servidores externos en la nube para el streaming de video y audio.

### Capacidades Principales:
* **Llamada Perpetua 24/7 P2P:** Auto-descubrimiento en red LAN por UDP (`37990`), enlace de video HD TCP (`37991`) y audio bidireccional UDP PCM 16 kHz (`37992`) con auto-reconexión infinita ante cortes de energía o caídas de red.
* **Compatibilidad Universal de Cámaras:** Soporta cámaras Dahua PoE (RTSP dual con conmutación inteligente), Webcams USB (DirectShow en Windows, V4L2 en Linux) y cámaras IP universales (RTSP/HTTP/ONVIF).
* **Audio Full-Duplex:** Transmisión de micrófono y reproducción por parlante/altavoz con control de ganancia y volumen.
* **Cinemática Fluida Sub-Píxel:** Zoom suave y paneo cinemático sin saltos bruscos (`0.45` omega) con oscilación automática en reposo.
* **Ocultamiento Inteligente de Cámaras:** Oculta automáticamente la vista panorámica cuando el foco está en la cámara 1 a 1 y viceversa (sin recuadros PiP distractores).
* **Moderación en 2 Etapas:** Detección de señas ofensivas (dedo medio, cuernos, pistola, pulgar abajo, uso de celular) con blureo automático en tiempo real y captura de evidencia forense sin blur en `evidencias_mal_uso/`.
* **Copiloto Interpretativo Puntual (Gemini API):** Auditoría bajo demanda y perfilamiento en conexiones 1 a 1 con estricto modo de ahorro de tokens.
* **Juegos Kinect AR con Gesto de Pellizco (🤏):** Botón flotante accesible en pantalla para jugar en solitario, multijugador local o Portal vs Portal en llamada (Air Pong, Neon Slicer, Pinch Pop Burbujas y Lienzo Aéreo).

---

## 📦 2. Contenido del Paquete (`DeaCam_Raspberry.tar.gz`)

```text
DeaCam/
├── portal_llamada_24_7.py       # Lanzador ligero de la Llamada 24/7 en Pantalla Completa
├── visor_dahua.py               # Panel Administrador React Glassmorphism
├── p2p_portal.py                # Motor P2P 24/7 LAN + Streaming Audio/Video
├── ai_engine.py                 # Motor de IA local (YOLOX, YuNet, MediaPipe 21 pts)
├── kinect_games.py              # Motor de Juegos Tipo Kinect AR (Gestos y Pellizco)
├── gemini_verifier.py           # Copiloto Interpretativo Gemini API (Ahorro Tokens)
├── autostart_raspberry.sh       # Instalador y demonio Watchdog para Raspberry Pi
├── autostart_windows.bat        # Lanzador para Windows
├── requirements.txt             # Dependencias Python
├── config_portal.json           # Archivo de configuración persistente
├── models/                      # Modelos ONNX pre-descargados (Cero dependencias externas)
│   ├── face_detection_yunet_2023mar.onnx
│   ├── object_detection_yolox_2022nov.onnx
│   ├── palm_detection_mediapipe_2023feb.onnx
│   ├── handpose_estimation_mediapipe_2023feb.onnx
│   ├── facial_expression_recognition_mobilefacenet_2022july.onnx
│   └── genderage.onnx
├── capturas/                    # Directorio de fotos/videos grabados
└── evidencias_mal_uso/          # Directorio de evidencias forenses automáticas
```

---

## 🍓 3. Instalación en Raspberry Pi (Paso a Paso)

### Requisitos de Hardware:
* Raspberry Pi 4 (4GB/8GB) o Raspberry Pi 5.
* Sistema Operativo: **Raspberry Pi OS (64-bit)** con entorno de escritorio.
* Cámara conectada (Webcam USB o cámara IP en el mismo switch/router Ethernet).
* Parlante / Bocina y Micrófono (USB o tarjeta de audio 3.5mm/HDMI).
* Pantalla o proyector HDMI.

---

### Paso 3.1: Transferir el paquete al Raspberry Pi
Desde tu computadora de desarrollo o administrador, abre una terminal en la carpeta del proyecto y ejecuta:

```bash
# En Windows PowerShell o Linux/Mac:
scp DeaCam_Raspberry.tar.gz pi@<IP_DEL_RASPBERRY>:~/
```
*(Reemplaza `<IP_DEL_RASPBERRY>` por la IP del Raspberry en la red local, por ejemplo: `192.168.1.150`).*

---

### Paso 3.2: Descomprimir y otorgar permisos
Conéctate por SSH al Raspberry Pi:
```bash
ssh pi@<IP_DEL_RASPBERRY>
```
Descomprime el paquete dentro de una carpeta:
```bash
mkdir -p ~/DeaCam
tar -xzvf ~/DeaCam_Raspberry.tar.gz -C ~/DeaCam/
cd ~/DeaCam
chmod +x autostart_raspberry.sh
```

---

### Paso 3.3: Instalación Automatizada con 1 Comando
Ejecuta el script instalador oficial:
```bash
./autostart_raspberry.sh --install
```

#### ¿Qué hace este instalador automáticamente?
1. Instala paquetes del sistema necesarios (`python3-tk`, `python3-pil`, `libportaudio2`, `x11-xserver-utils`, `unclutter`).
2. Instala dependencias Python (`opencv-python`, `numpy`, `pillow`, `requests`, `sounddevice`).
3. Genera el script guardián **Watchdog** (`run_portal_watchdog_rpi.sh`):
   - Desactiva el protector de pantalla, el modo reposo HDMI y el DPMS.
   - Oculta el cursor del mouse automáticamente si está inactivo.
   - Si la corriente eléctrica se corta o el proceso se cierra, **se reinicia solo en 2 segundos**.
4. Registra la aplicación en el inicio automático del escritorio (`~/.config/autostart/deacam-portal-24-7.desktop`).

---

## 🪟 4. Instalación en PC / Servidor Windows

Si el otro extremo de la llamada es una PC con Windows:

1. Asegúrate de tener instalado **Python 3.10+**.
2. Abre PowerShell en la carpeta `C:\gestweb\DeaCam`:
   ```powershell
   pip install -r requirements.txt
   ```
3. Para iniciar la llamada en pantalla completa:
   - Doble clic en `autostart_windows.bat` o ejecuta:
   ```powershell
   python portal_llamada_24_7.py
   ```
4. Para abrir el Panel de Administrador completo:
   ```powershell
   python visor_dahua.py
   ```

---

## 🎮 5. Interacción y Operación del Portal

### Controles para el Administrador con Periféricos (Mouse / Teclado)
Cuando el Portal está en pantalla completa limpia sin bordes:
* **F12**, **Esc**, **Ctrl + A** o **Clic Derecho**: Abre el **Panel de Administración** encima para ajustar cámaras, sensibilidades, red o audio.
* **F11** o **Doble Clic Izquierdo**: Alterna entre pantalla completa sin bordes y ventana normal.
* **Mover el Mouse**: Muestra temporalmente el cursor (se vuelve a ocultar tras 2.5 segundos de inactividad).

### Interacción por Gestos AR para el Usuario (Sin Teclado ni Mouse)
* **Botón Flotante (`🎮 JUEGOS KINECT AR`):** Aparece en la parte superior del espejo.
* **Gesto de Pellizco (`🤏` Pulgar + Índice):** Al colocar la mano sobre cualquier botón o tarjeta y juntar los dedos en pinza, se activa la opción o se selecciona el juego.
* **Juegos Disponibles:**
  1. `Air Pong Neón`: Raquetas holográficas controladas con las manos.
  2. `Neon Slicer`: Corta esferas de energía con estelas de movimiento.
  3. `Pinch Pop Burbujas`: Pellizca las burbujas flotantes para reventarlas.
  4. `Lienzo Aéreo`: Dibuja con luz sobre el espejo juntando pulgar e índice.

---

## ⚙️ 6. Configuración de Red y Persistencia (`config_portal.json`)

Todos los parámetros se guardan automáticamente en `config_portal.json`:

```json
{
  "auto_save_prefs": true,
  "auto_join_call": true,
  "auto_open_portal_win": true,
  "target_peer_ip": "Auto-Descubrimiento LAN P2P (24/7)",
  "portal_call_view_mode": "Automático 24/7 (Muestra Par Remoto en Llamada / Espejo Local en Espera)",
  "hide_inactive_cam_pip": true,
  "camera_source_mode": "Dahua Dual / Simple PoE (RTSP Automático)",
  "enable_speaker": true,
  "enable_mic": true,
  "smooth_omega": 0.45,
  "max_zoom": 1.65,
  "gemini_enabled": false,
  "gemini_api_key": ""
}
```

> [!TIP]
> Si los equipos están en la misma red local, déjalo en **`Auto-Descubrimiento LAN P2P (24/7)`**. Los nodos emitirán paquetes broadcast por el puerto `37990` y se enlazarán en llamada sin necesidad de configurar IPs manuales.

---

## 🛠️ 7. Preguntas Frecuentes y Diagnóstico

### ¿Qué puertos deben estar libres en el Firewall de la red LAN?
* `37990 UDP`: Auto-descubrimiento de equipos en la misma red.
* `37991 TCP`: Streaming de video HD Zero-Lag.
* `37992 UDP`: Audio bidireccional PCM (Micrófono / Parlante) y telemetría de juegos.

### ¿Cómo cambiar entre cámara Dahua PoE y Webcam USB en el Raspberry Pi?
Abre el Panel Admin (`python3 visor_dahua.py`) y en la tarjeta **Portal Llamada Perpetua 24/7**, cambia el menú desplegable **Entrada de Cámara** a **`Cámara USB / Webcam Local (Universal)`**. Haz clic en **`💾 Guardar Preferencias`**.

### ¿Cómo probar el parlante/altavoz?
En el Panel Admin, abre **`🔊 Config P2P / Parlante`** y haz clic en **`🔔 Probar Parlante (Tono de Prueba)`**. Sonará un acorde limpio para confirmar que el dispositivo de salida de audio funciona correctamente.
