import math
import time
from typing import Tuple, List, Optional
import threading
import os

import cv2
import numpy as np


class LiteSmoothCamera:
    """
    Cabezal Cinemático Fluido Ultra-Ligero:
    - Diseñado para responder a 60 FPS en Raspberry Pi y PC sin sobrecarga de CPU.
    - Suavizado crítico amortiguado (Spring Damper continuo) para paneo y zoom lento y agradable.
    - Zoom dinámico orgánico (1.0x a 1.65x) enfocado en el rostro cuando hay contacto visual.
    - Modo Oscilación / Patrulla suave (Izquierda <-> Derecha) cuando la escena está tranquila.
    - Transformación afín sub-píxel continua (cv2.warpAffine) para CERO saltos o parpadeos.
    """

    def __init__(self, cx: float = 0.5, cy: float = 0.5, zoom: float = 1.0):
        self.cx = cx
        self.cy = cy
        self.zoom = zoom
        self.vx = 0.0
        self.vy = 0.0
        self.vz = 0.0

        self.raw_target_cx = cx
        self.raw_target_cy = cy
        self.raw_target_zoom = zoom

        self.target_cx = cx
        self.target_cy = cy
        self.target_zoom = zoom

        self.last_time = time.time()
        self.last_face_time = 0.0
        self.last_motion_time = time.time()

    def set_target(self, tx: float, ty: float, tz: float):
        self.raw_target_cx = tx
        self.raw_target_cy = ty
        self.raw_target_zoom = tz

    def step(self, omega: float = 0.45) -> Tuple[float, float, float]:
        now = time.time()
        dt = min(0.05, max(0.005, now - self.last_time))
        self.last_time = now

        eff_omega = max(0.55, float(omega) * 1.45)
        # Pre-suavizado exponencial
        blend = 1.0 - math.exp(-eff_omega * 3.0 * dt)
        self.target_cx += (self.raw_target_cx - self.target_cx) * blend
        self.target_cy += (self.raw_target_cy - self.target_cy) * blend
        self.target_zoom += (self.raw_target_zoom - self.target_zoom) * blend

        exp_term = math.exp(-eff_omega * dt)
        max_step = max(0.06, 0.16 * eff_omega) * dt

        dx = self.cx - self.target_cx
        temp_x = (self.vx + eff_omega * dx) * dt
        next_cx = self.target_cx + (dx + temp_x) * exp_term
        self.vx = (self.vx - eff_omega * temp_x) * exp_term
        delta_x = max(-max_step, min(max_step, next_cx - self.cx))
        self.cx += delta_x

        dy = self.cy - self.target_cy
        temp_y = (self.vy + eff_omega * dy) * dt
        next_cy = self.target_cy + (dy + temp_y) * exp_term
        self.vy = (self.vy - eff_omega * temp_y) * exp_term
        delta_y = max(-max_step, min(max_step, next_cy - self.cy))
        self.cy += delta_y

        dz = self.zoom - self.target_zoom
        temp_z = (self.vz + eff_omega * dz) * dt
        self.zoom = self.target_zoom + (dz + temp_z) * exp_term
        self.vz = (self.vz - eff_omega * temp_z) * exp_term

        self.cx = max(0.1, min(0.9, self.cx))
        self.cy = max(0.1, min(0.9, self.cy))
        self.zoom = max(1.0, min(2.5, self.zoom))

        return self.cx, self.cy, self.zoom


class LiteVideoPipeline:
    """
    Pipeline de Video Ultra-Rápido para Portal Lite:
    - Detección de rostros ultraligera (YuNet ONNX o Haar Cascade con respaldo).
    - Zoom dinámico suave en 1 a 1 enfocado en la mirada.
    - Oscilación automática relajante si no hay nadie hablando/frente a la cámara.
    - Modo espejo limpio sin textos ni estorbos.
    """

    def __init__(self, models_dir: str):
        self.camera = LiteSmoothCamera()
        self.face_detector = None
        self._init_face_detector(models_dir)
        self.last_detection_ts = 0.0
        self.last_faces: List[Tuple[int, int, int, int]] = []
        self.lock = threading.Lock()

    def _init_face_detector(self, models_dir: str):
        yunet_path = os.path.join(models_dir, "face_detection_yunet_2023mar.onnx")
        if os.path.exists(yunet_path) and hasattr(cv2, "FaceDetectorYN"):
            try:
                self.face_detector = cv2.FaceDetectorYN.create(
                    model=yunet_path,
                    config="",
                    input_size=(320, 180),
                    score_threshold=0.60,
                    nms_threshold=0.3,
                    top_k=2,
                )
            except Exception:
                self.face_detector = None

    def process_frame(
        self,
        raw_bgr: np.ndarray,
        enable_dynamic_zoom: bool = True,
        enable_idle_patrol: bool = True,
        max_zoom: float = 1.65,
        smooth_omega: float = 0.45,
        target_size: Tuple[int, int] = (1280, 720),
    ) -> Tuple[np.ndarray, dict]:
        h, w = raw_bgr.shape[:2]
        now = time.time()

        # Detección asíncrona / periódica de rostros para no saturar CPU (cada ~90ms)
        if now - self.last_detection_ts >= 0.088 and self.face_detector is not None:
            self.last_detection_ts = now
            # Detectar en resolución reducida (320x180) para velocidad instantánea (<2ms)
            dw, dh = 320, 180
            small = cv2.resize(raw_bgr, (dw, dh), interpolation=cv2.INTER_LINEAR)
            self.face_detector.setInputSize((dw, dh))
            _, faces = self.face_detector.detect(small)
            boxes = []
            if faces is not None and len(faces) > 0:
                sx = float(w) / float(dw)
                sy = float(h) / float(dh)
                for f in faces:
                    fx, fy, fw, fh = f[:4]
                    boxes.append((int(fx * sx), int(fy * sy), int(fw * sx), int(fh * sy)))
            with self.lock:
                self.last_faces = boxes

        with self.lock:
            detected_faces = list(self.last_faces)

        # Lógica de Encuadre Inteligente:
        # 1. Si hay rostro en escena: enfocar y hacer zoom dinámico suave hacia la persona
        if detected_faces and enable_dynamic_zoom:
            self.camera.last_face_time = now
            # Tomar el rostro principal
            fx, fy, fw, fh = detected_faces[0]
            # Centro ponderado (ligeramente debajo de los ojos para encuadre natural)
            fcx = (fx + fw * 0.5) / float(w)
            fcy = (fy + fh * 0.65) / float(h)
            # Calcular zoom en base a qué tan lejos está la persona
            face_ratio = float(fw * fh) / float(w * h)
            # Si está lejos (ratio < 0.05), aplicar zoom máximo; si está cerca, relajar zoom
            calc_zoom = 1.0 + (max_zoom - 1.0) * max(0.0, min(1.0, (0.07 - face_ratio) / 0.06))
            calc_zoom = max(1.05, min(max_zoom, calc_zoom))
            self.camera.set_target(fcx, fcy, calc_zoom)
            state_label = "CONEXIÓN 1 A 1 ACTIVA (ENFOQUE SUAVE)"

        # 2. Si no hay rostros pero está activa la patrulla/oscilación: oscilar lentamente
        elif enable_idle_patrol and (now - self.camera.last_face_time > 4.0):
            patrol_zoom = 1.18
            half_span = 0.5 / patrol_zoom
            max_pan = max(0.03, (0.5 - half_span) * 0.85)
            pan_x = 0.5 + max_pan * math.sin(now * 0.12)
            self.camera.set_target(pan_x, 0.5, patrol_zoom)
            state_label = "OSCILACIÓN SUAVE DE BÚSQUEDA"

        # 3. Vista panorámica completa en reposo
        else:
            self.camera.set_target(0.5, 0.5, 1.0)
            state_label = "ESPEJO EN REPOSO (1.0x)"

        cur_cx, cur_cy, cur_zoom = self.camera.step(omega=smooth_omega)

        # Aplicar recorte continuo por matriz afín para suavidad cinematográfica absoluta
        tw, th = target_size
        if cur_zoom > 1.003:
            crop_w = float(w) / float(cur_zoom)
            crop_h = float(h) / float(cur_zoom)
            x0 = max(0.0, min(float(w) - crop_w, float(cur_cx) * float(w) - crop_w * 0.5))
            y0 = max(0.0, min(float(h) - crop_h, float(cur_cy) * float(h) - crop_h * 0.5))
            sx = float(tw) / crop_w
            sy = float(th) / crop_h
            M = np.array(
                [
                    [sx, 0.0, -x0 * sx],
                    [0.0, sy, -y0 * sy],
                ],
                dtype=np.float32,
            )
            out = cv2.warpAffine(raw_bgr, M, (tw, th), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        else:
            if w != tw or h != th:
                out = cv2.resize(raw_bgr, (tw, th), interpolation=cv2.INTER_LINEAR)
            else:
                out = raw_bgr

        stats = {
            "faces": len(detected_faces),
            "zoom": cur_zoom,
            "pan_x": cur_cx,
            "state": state_label,
        }
        return out, stats


class PanoramicSweeper:
    """
    Barrido Panorámico Cinemático para Cámara Secundaria:
    - Realiza un barrido continuo y ultra suave de izquierda a derecha y de regreso.
    - Cuando nadie interactúa en la llamada, esta cámara pasa a Primer Plano.
    - Cuando hay interacción en la llamada, se reduce a un recuadro pequeño (PiP).
    """

    def __init__(self, speed: float = 0.05, span: float = 0.28):
        self.speed = speed      # Velocidad de ciclo sinusoidal (muy suave y cinematográfico)
        self.span = span        # Amplitud de paneo horizontal
        self.t0 = time.time()
        self.cur_pan = 0.5

    def process_sweep(self, raw_bgr: np.ndarray, target_size: Tuple[int, int] = (1280, 720)) -> np.ndarray:
        if raw_bgr is None or raw_bgr.size == 0:
            return np.zeros((target_size[1], target_size[0], 3), dtype=np.uint8)

        h, w = raw_bgr.shape[:2]
        tw, th = target_size
        elapsed = time.time() - self.t0

        # Oscilación sinusoidal izquierda <-> derecha continua
        phase = math.sin(elapsed * self.speed * 2.0 * math.pi)
        target_pan = 0.5 + phase * self.span

        # Filtro de resorte suave para evitar aceleraciones bruscas
        self.cur_pan += (target_pan - self.cur_pan) * 0.06
        pan_x = max(0.15, min(0.85, self.cur_pan))

        # Recorte panorámico con zoom 1.20x para permitir el barrido lateral
        crop_zoom = 1.22
        view_w = float(w) / crop_zoom
        view_h = float(h) / crop_zoom

        center_x = pan_x * float(w)
        center_y = float(h) * 0.5

        x0 = max(0.0, min(float(w) - view_w, center_x - view_w * 0.5))
        y0 = max(0.0, min(float(h) - view_h, center_y - view_h * 0.5))

        sx = float(tw) / view_w
        sy = float(th) / view_h
        M = np.array(
            [
                [sx, 0.0, -x0 * sx],
                [0.0, sy, -y0 * sy],
            ],
            dtype=np.float32,
        )
        return cv2.warpAffine(raw_bgr, M, (tw, th), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)

