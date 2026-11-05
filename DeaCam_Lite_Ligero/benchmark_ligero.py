"""Mide cuanto cuesta un ciclo de video en ESTE equipo: version ligera vs normal.
Uso:  python benchmark_ligero.py
No necesita camaras ni red. Usa imagenes sinteticas.
"""
import os
os.environ["OPENCV_LOG_LEVEL"] = "SILENT"
import time
import tkinter as tk

import cv2
import numpy as np
from PIL import Image, ImageTk

from lite_pipeline import LiteVideoPipeline, PanoramicSweeper

N = 40
rng = np.random.default_rng(1)


def foto(w, h):
    base = rng.integers(0, 255, (h // 8, w // 8, 3), dtype=np.uint8)
    return cv2.resize(base, (w, h), interpolation=cv2.INTER_LINEAR)


def ms(fn):
    fn()
    t = time.perf_counter()
    for _ in range(N):
        fn()
    return (time.perf_counter() - t) / N * 1000.0


def medir(nombre, cap, canvas, quality, usa_cam2, display, fps):
    pipe = LiteVideoPipeline("models")
    pipe.camera.zoom = 1.3  # fuerza la ruta con zoom (la mas cara)
    pipe.camera.target_zoom = pipe.camera.raw_target_zoom = 1.3
    sweep = PanoramicSweeper()
    cam1, cam2 = foto(*cap), foto(*cap)
    remoto = foto(*canvas)
    ok, enc = cv2.imencode(".jpg", remoto, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    jpg = np.frombuffer(enc.tobytes(), dtype=np.uint8)
    root = tk.Tk()
    root.withdraw()

    def pintar():
        rgb = cv2.cvtColor(cv2.resize(remoto, display, interpolation=cv2.INTER_NEAREST), cv2.COLOR_BGR2RGB)
        ImageTk.PhotoImage(image=Image.fromarray(rgb), master=root)

    t = {
        "procesar camara 1 (zoom)": ms(lambda: pipe.process_frame(cam1, target_size=canvas)),
        "codificar JPEG (envio)": ms(lambda: cv2.imencode(".jpg", remoto, [int(cv2.IMWRITE_JPEG_QUALITY), quality])),
        "decodificar JPEG (recibo)": ms(lambda: cv2.imdecode(jpg, cv2.IMREAD_COLOR)),
        "barrido camara 2": ms(lambda: sweep.process_sweep(cam2, target_size=canvas)) if usa_cam2 else 0.0,
        "componer + pintar en pantalla": ms(pintar),
    }
    root.destroy()
    # Carga real por segundo: cada tarea se multiplica por las veces que se hace en un segundo
    veces = {
        "procesar camara 1 (zoom)": fps["video"],
        "codificar JPEG (envio)": fps["envio"],
        "decodificar JPEG (recibo)": fps["recibo"],
        "barrido camara 2": fps["cam2"],
        "componer + pintar en pantalla": fps["video"],
    }
    print(f"\n== {nombre}  (camara {cap[0]}x{cap[1]}, canvas {canvas[0]}x{canvas[1]}, pintado {display[0]}x{display[1]}, JPEG q{quality})")
    carga = 0.0
    for k, v in t.items():
        carga += v * veces[k]
        print(f"   {k:32s} {v:6.1f} ms  x {veces[k]:>2} por seg")
    print(f"   {'CARGA TOTAL':32s} {carga:6.0f} ms de CPU por cada 1000 ms")
    return carga


print("OpenCV", cv2.__version__, "| CPUs:", os.cpu_count())
a = medir("NORMAL", (1280, 720), (1280, 720), 78, True, (1280, 720),
          {"video": 40, "envio": 40, "recibo": 40, "cam2": 40})
b = medir("LIGERA", (640, 360), (854, 480), 65, True, (1024, 576),
          {"video": 15, "envio": 10, "recibo": 12, "cam2": 8})
print(f"\nLa version ligera usa ~{a / b:.1f}x menos CPU que la normal.")
print("Todo corre en un solo hilo principal: si la CARGA TOTAL de la LIGERA pasa de ~700, el equipo")
print("no llega a 15 fps; bajar CANVAS_W/H a 640x360 o RENDER_FPS a 10 en portal_lite_24_7.py.")
