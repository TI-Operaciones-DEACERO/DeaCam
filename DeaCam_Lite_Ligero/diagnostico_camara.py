"""Diagnostico de camara USB para DeaCam Lite (Windows).
Prueba cada indice y backend, intenta LEER cuadros y guarda una foto de cada intento exitoso.
Uso: python diagnostico_camara.py
"""
import os
import sys
os.environ["OPENCV_LOG_LEVEL"] = "SILENT"  # sin avisos ruidosos de OpenCV
import cv2

print("Python", sys.version.split()[0], "| OpenCV", cv2.__version__)
BACKENDS = [("DSHOW", cv2.CAP_DSHOW), ("MSMF", cv2.CAP_MSMF), ("ANY", cv2.CAP_ANY)]

for idx in range(4):
    for name, be in BACKENDS:
        for forzar in (True, False):
            tag = f"idx={idx} backend={name} 1280x720={'si' if forzar else 'no'}"
            cap = cv2.VideoCapture(idx, be)
            if not cap.isOpened():
                print(f"[--] {tag}: no abre")
                cap.release()
                continue
            if forzar:
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
            ok, frame = False, None
            for _ in range(15):  # las primeras lecturas suelen fallar mientras calienta
                ok, frame = cap.read()
                if ok and frame is not None:
                    break
            if ok and frame is not None:
                brillo = float(frame.mean())
                estado = "NEGRO (tapa/privacidad?)" if brillo < 3 else "OK"
                fn = f"diag_idx{idx}_{name}_{'720' if forzar else 'def'}.jpg"
                cv2.imwrite(fn, frame)
                print(f"[OK] {tag}: {frame.shape[1]}x{frame.shape[0]} brillo={brillo:.0f} {estado} -> {fn}")
            else:
                print(f"[XX] {tag}: abre pero NO entrega imagen")
            cap.release()
