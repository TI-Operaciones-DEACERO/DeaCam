#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Simulador de Participante Remoto para Pruebas en Windows
=======================================================
Permite probar la videollamada 1 a 1 y grupal en la misma computadora.
Se conecta a DeaCam Portal Lite 24/7 y transmite un video simulado en tiempo real
con rostro en movimiento y reloj, para verificar:
1. Recepción de video 1 a 1 en pantalla completa.
2. Entrada del participante remoto.
3. Audio y respuesta en tiempo real.
"""

import sys
import time
import math
import json
import socket
import struct
import cv2
import numpy as np

def run_test_peer(target_ip="127.0.0.1", port=37991, peer_name="Participante Remoto (Prueba)", duration_sec=60):
    print(f"🚀 Conectando simulador a {target_ip}:{port} como '{peer_name}'...")
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    try:
        s.connect((target_ip, port))
    except Exception as e:
        print(f"❌ Error al conectar con Portal Lite ({e}). Asegúrate de que portal_lite_24_7.py esté abierto.")
        return

    magic = b"DCMLITE"
    hello = json.dumps({
        "hostname": peer_name,
        "audio_port": 37992,
        "node_id": "test_peer_sim_99",
    }).encode("utf-8")
    s.sendall(magic + struct.pack("!BI", 1, len(hello)) + hello)
    print("✅ Enlace P2P establecido con éxito. Transmitiendo video de prueba 1 a 1...")

    t0 = time.time()
    w, h = 1280, 720
    frame_idx = 0

    while time.time() - t0 < duration_sec:
        # Generar cuadro de video cinemático animado simulando cámara remota
        now = time.time()
        elapsed = now - t0
        frame = np.zeros((h, w, 3), dtype=np.uint8)

        # Fondo con gradiente elegante
        for y in range(h):
            ratio = y / h
            frame[y, :, :] = (int(20 + 30 * ratio), int(15 + 20 * ratio), int(25 + 35 * ratio))

        # Rostro / Cabeza simulada que se mueve suavemente (contacto visual)
        cx = int(w * (0.5 + 0.12 * math.sin(elapsed * 0.8)))
        cy = int(h * (0.48 + 0.06 * math.cos(elapsed * 0.6)))
        radius = int(110 + 20 * math.sin(elapsed * 0.4))

        # Aura
        cv2.circle(frame, (cx, cy), radius + 25, (56, 189, 248), 3, cv2.LINE_AA)
        # Cabeza
        cv2.circle(frame, (cx, cy), radius, (240, 200, 160), -1, cv2.LINE_AA)

        # Ojos que parpadean y miran a la cámara
        eye_y = cy - int(radius * 0.15)
        eye_dx = int(radius * 0.35)
        blink = (int(elapsed * 3) % 15 == 0)
        eye_h = 3 if blink else 10
        cv2.ellipse(frame, (cx - eye_dx, eye_y), (14, eye_h), 0, 0, 360, (20, 20, 20), -1, cv2.LINE_AA)
        cv2.ellipse(frame, (cx + eye_dx, eye_y), (14, eye_h), 0, 0, 360, (20, 20, 20), -1, cv2.LINE_AA)
        if not blink:
            # Pupila con reflejo
            cv2.circle(frame, (cx - eye_dx + 1, eye_y), 4, (56, 189, 248), -1, cv2.LINE_AA)
            cv2.circle(frame, (cx + eye_dx + 1, eye_y), 4, (56, 189, 248), -1, cv2.LINE_AA)

        # Sonrisa
        cv2.ellipse(frame, (cx, cy + int(radius * 0.35)), (int(radius * 0.35), int(radius * 0.2)), 0, 20, 160, (40, 40, 40), 4, cv2.LINE_AA)

        # Tarjeta de estado de videollamada
        cv2.rectangle(frame, (40, 40), (620, 130), (15, 23, 42), -1)
        cv2.rectangle(frame, (40, 40), (620, 130), (56, 189, 248), 2)
        cv2.putText(frame, "🟢 LLAMADA 1 A 1 ACTIVA (ENLACE P2P)", (60, 75), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (56, 189, 248), 2, cv2.LINE_AA)
        cv2.putText(frame, f"Participante: {peer_name} | {elapsed:.1f}s", (60, 110), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (226, 232, 240), 1, cv2.LINE_AA)

        # Enviar cuadro JPEG
        ok, enc = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
        if ok:
            jpg = enc.tobytes()
            pkt = magic + struct.pack("!BI", 2, len(jpg)) + jpg
            try:
                s.sendall(pkt)
            except Exception:
                break

        time.sleep(0.033)  # ~30 FPS
        frame_idx += 1

    try:
        s.close()
    except Exception:
        pass
    print("🏁 Fin de la simulación de prueba.")

if __name__ == "__main__":
    dur = int(sys.argv[1]) if len(sys.argv) > 1 else 90
    name = sys.argv[2] if len(sys.argv) > 2 else "Compañero Remoto (Prueba 1 a 1)"
    run_test_peer(duration_sec=dur, peer_name=name)
