import os
import sys
import time
import math
import json
import threading
from datetime import datetime
from dataclasses import dataclass, field
from typing import List, Tuple, Optional

import cv2
import numpy as np


@dataclass
class HandGestureInfo:
    bbox: Tuple[int, int, int, int]  # x, y, w, h
    landmarks: np.ndarray  # (21, 3)
    gesture_name: str
    is_offensive: bool = False  # Solo True cuando el acto está 100% confirmado por articulaciones
    is_wave_pose: bool = False
    confidence: float = 0.0
    category: str = "normal"


@dataclass
class PersonAnalysis:
    person_id: int
    bbox: Tuple[int, int, int, int]  # (x, y, w, h)
    confidence: float
    has_face: bool = False
    face_bbox: Optional[Tuple[int, int, int, int]] = None
    landmarks: Optional[np.ndarray] = None
    eye_contact: bool = False
    gaze_score: float = 0.0
    is_waving: bool = False
    wave_score: float = 0.0
    motion_level: float = 0.0
    label: str = "Persona"

    # Atributos detallados de Perfil IA
    gender: str = "Analizando..."
    age: int = 0
    age_range: str = "--"
    expression: str = "Neutral"
    upper_color: str = "--"
    lower_color: str = "--"
    style_desc: str = "Casual"
    economic_profile: str = "Medio / Estándar"
    physical_desc: str = "--"
    accessories: str = "Ninguno"
    posture: str = "De pie / Activo"
    is_offensive: bool = False
    offensive_reason: str = ""
    hand_boxes: List[Tuple[int, int, int, int]] = field(default_factory=list)


class SmoothSpringCamera:
    """
    Cabezal Fluido de Cine (Ultra-Soft Cinema Fluid Head + Critically Damped Spring):
    - Filtra el objetivo con doble amortiguación exponencial + límite de velocidad máxima
      para que tanto el paneo como el zoom sean ultra lentos, orgánicos y cómodos a la vista.
    - Sin saltos bruscos ni micro-vibraciones.
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

    def set_target(self, tx: float, ty: float, tz: float, deadzone_pos: float = 0.045, deadzone_zoom: float = 0.055):
        if math.hypot(tx - self.raw_target_cx, ty - self.raw_target_cy) > deadzone_pos:
            self.raw_target_cx = tx
            self.raw_target_cy = ty
        if abs(tz - self.raw_target_zoom) > deadzone_zoom:
            self.raw_target_zoom = tz

    def step(self, omega: float = 0.45) -> Tuple[float, float, float]:
        now = time.time()
        dt = min(0.05, max(0.004, now - self.last_time))
        self.last_time = now

        # Escalar omega para que el movimiento se sienta como grúa cinematográfica ultra suave y lenta (~3.5s)
        eff_omega = max(0.58, float(omega) * 1.48)

        # Pre-suavizado exponencial del objetivo (evita cualquier arranque brusco)
        target_blend = 1.0 - math.exp(-eff_omega * 3.2 * dt)
        self.target_cx += (self.raw_target_cx - self.target_cx) * target_blend
        self.target_cy += (self.raw_target_cy - self.target_cy) * target_blend
        self.target_zoom += (self.raw_target_zoom - self.target_zoom) * target_blend

        exp_term = math.exp(-eff_omega * dt)
        max_step_pos = max(0.065, 0.16 * eff_omega) * dt

        dx = self.cx - self.target_cx
        temp_x = (self.vx + eff_omega * dx) * dt
        next_cx = self.target_cx + (dx + temp_x) * exp_term
        self.vx = (self.vx - eff_omega * temp_x) * exp_term
        delta_x = max(-max_step_pos, min(max_step_pos, next_cx - self.cx))
        self.cx += delta_x

        dy = self.cy - self.target_cy
        temp_y = (self.vy + eff_omega * dy) * dt
        next_cy = self.target_cy + (dy + temp_y) * exp_term
        self.vy = (self.vy - eff_omega * temp_y) * exp_term
        delta_y = max(-max_step_pos, min(max_step_pos, next_cy - self.cy))
        self.cy += delta_y

        omega_z = eff_omega * (0.95 if self.target_zoom >= self.zoom else 0.72)
        exp_z = math.exp(-omega_z * dt)
        dz = self.zoom - self.target_zoom
        temp_z = (self.vz + omega_z * dz) * dt
        next_zoom = self.target_zoom + (dz + temp_z) * exp_z
        self.vz = (self.vz - omega_z * temp_z) * exp_z
        max_step_zoom = max(0.12, 0.28 * omega_z) * dt
        delta_z = max(-max_step_zoom, min(max_step_zoom, next_zoom - self.zoom))
        self.zoom += delta_z

        self.zoom = max(1.0, self.zoom)
        half = 0.5 / self.zoom
        self.cx = max(half, min(1.0 - half, self.cx))
        self.cy = max(half, min(1.0 - half, self.cy))

        return self.cx, self.cy, self.zoom


class SmartCameraAI:
    """
    Motor de IA Desacoplado con Moderación Estricta de Actos Confirmados (Cero Falsos Positivos "Sospechosos")
    y Captura Instantánea de Evidencias de Mal Uso:
    - NUNCA blurea por "sospecha": exige confirmación real de las 21 articulaciones 3D de la mano (confianza alta)
      de que el acto ofensivo YA se está efectuando en ese momento.
    - Al confirmarse el acto, dispara un Super Screenshot Rápido en segundo plano (con el cuadro original nítido
      + recorte de acercamiento + datos biométricos del infractor) y lo guarda en `evidencias_mal_uso/`.
    """

    def __init__(self, models_dir: str, evidences_dir: Optional[str] = None, cam_label: str = "Cam"):
        self.models_dir = models_dir
        self.cam_label = cam_label
        if evidences_dir is None:
            base_dir = os.path.dirname(os.path.abspath(models_dir))
            evidences_dir = os.path.join(base_dir, "evidencias_mal_uso")
        self.evidences_dir = evidences_dir
        os.makedirs(self.evidences_dir, exist_ok=True)

        if models_dir not in sys.path:
            sys.path.append(models_dir)

        self.yunet_path = os.path.join(models_dir, "face_detection_yunet_2023mar.onnx")
        self.yolox_path = os.path.join(models_dir, "object_detection_yolox_2022nov.onnx")
        self.genderage_path = os.path.join(models_dir, "genderage.onnx")
        self.palm_path = os.path.join(models_dir, "palm_detection_mediapipe_2023feb.onnx")
        self.handpose_path = os.path.join(models_dir, "handpose_estimation_mediapipe_2023feb.onnx")

        self.face_detector = None
        self.person_net = None
        self.genderage_net = None
        self.palm_detector = None
        self.handpose_detector = None
        self._init_models()

        self.yolox_size = (416, 416)
        self._init_yolox_grids()

        self.bg_subtractor = cv2.createBackgroundSubtractorMOG2(
            history=160, varThreshold=28, detectShadows=False
        )
        self.wave_history = {}
        self.gaze_ema = {}
        self.age_ema = {}
        self.gender_votes = {}

        # Retención corta (0.8s) solo mientras se está efectuando el acto confirmado
        self.offensive_hold = {}  # person_id -> (expira_ts, razon, hand_boxes)
        # Pre-análisis temporal antes de enviar a Post-Análisis IA del frame
        self.pre_analysis_tracker = {}  # person_id -> {"cat": str, "count": int, "last_ts": float, "verified": bool}
        self.clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        self.last_evidence_time = {}  # person_id -> ultimo_ts de screenshot guardado (cooldown 3.5s)
        self.total_evidences_saved = 0
        self.last_evidence_file = ""

        # Reglas del catálogo de actos ofensivos confirmados
        self.moderation_rules = {
            "sensitivity": 0.78,
            "block_middle_finger": True,   # Dedo medio confirmado por 21 puntos
            "block_gun_gesture": True,     # Seña de arma apuntando confirmada
            "block_fist_threat": False,    # Desactivado por defecto para no confundir mano cerrada normal con amenaza
            "block_horns_insult": True,    # Cuernos / insulto confirmado
            "block_thumbs_down": True,     # Pulgar abajo confirmado
            "block_cellphone_use": True,   # Uso de teléfono celular / tomar fotografías o grabar la pantalla
            "block_pelvic_gesture": False, # Solo si el usuario lo solicita explícitamente
            "block_face_mockery": False,   # Desactivado por defecto para no blurear si alguien se rasca la cara o acomoda lentes
            "block_nudity_skin": False,    # Desactivado por defecto para evitar falsos positivos con ropa beige/naranja
            "hold_seconds": 0.85,          # Solo mantiene el blur mientras el acto se está ejecutando
        }

        self.spring = SmoothSpringCamera(0.5, 0.5, 1.0)
        self.state_lock = threading.Lock()

        self.last_attention_time = 0.0
        self.last_motion_time = time.time()
        self.attention_hold_seconds = 4.0
        self.idle_before_patrol_seconds = 4.5

        self.last_analyses: List[PersonAnalysis] = []
        self.last_hands: List[HandGestureInfo] = []
        self.last_yolox_phones: List[Tuple[Tuple[int, int, int, int], float]] = []
        self.global_motion_ratio = 0.0
        self.active_mode_banner = "🌐 CAMPO ABIERTO PANORÁMICO"
        self.is_engaged_active = False
        self.is_patrolling = False
        self.ai_cycle_count = 0

    def _init_models(self):
        if os.path.exists(self.yunet_path):
            try:
                self.face_detector = cv2.FaceDetectorYN.create(
                    self.yunet_path, "", (320, 320), 0.48, 0.3, 5000
                )
            except Exception as e:
                print(f"[AI] Error cargando YuNet: {e}")

        if os.path.exists(self.yolox_path):
            try:
                self.person_net = cv2.dnn.readNetFromONNX(self.yolox_path)
            except Exception as e:
                print(f"[AI] Error cargando YOLOX: {e}")

        if os.path.exists(self.genderage_path):
            try:
                self.genderage_net = cv2.dnn.readNetFromONNX(self.genderage_path)
            except Exception as e:
                print(f"[AI] Error cargando GenderAge: {e}")

        if os.path.exists(self.palm_path) and os.path.exists(self.handpose_path):
            try:
                from mp_palmdet import MPPalmDet
                from mp_handpose import MPHandPose
                # Umbral equilibrado para no perder palmas de perfil/mentón y validar luego en Post-Análisis 3D
                self.palm_detector = MPPalmDet(self.palm_path, scoreThreshold=0.42, nmsThreshold=0.3)
                self.handpose_detector = MPHandPose(self.handpose_path, confThreshold=0.60)
            except Exception as e:
                print(f"[AI] Error cargando HandPose MediaPipe: {e}")

    def _init_yolox_grids(self):
        grids = []
        expanded_strides = []
        for stride in (8, 16, 32):
            gh = self.yolox_size[0] // stride
            gw = self.yolox_size[1] // stride
            xv, yv = np.meshgrid(np.arange(gw), np.arange(gh))
            grid = np.stack((xv, yv), 2).reshape(1, -1, 2)
            grids.append(grid)
            expanded_strides.append(np.full((1, grid.shape[1], 1), stride))
        self.yolox_grids = np.concatenate(grids, axis=1)[0]
        self.yolox_strides = np.concatenate(expanded_strides, axis=1)[0]

    def _detect_persons_yolox(self, frame: np.ndarray, conf_thresh: float = 0.38) -> List[Tuple[Tuple[int, int, int, int], float]]:
        if self.person_net is None:
            self.last_yolox_phones = []
            return []

        h, w = frame.shape[:2]
        ih, iw = self.yolox_size
        scale = min(ih / h, iw / w)
        nh, nw = int(h * scale), int(w * scale)

        resized = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_NEAREST)
        padded = np.full((ih, iw, 3), 114, dtype=np.uint8)
        padded[:nh, :nw] = resized

        blob = cv2.dnn.blobFromImage(padded, 1.0, self.yolox_size, (0, 0, 0), swapRB=False)
        self.person_net.setInput(blob)
        outs = self.person_net.forward()[0].copy()

        outs[:, :2] = (outs[:, :2] + self.yolox_grids) * self.yolox_strides
        outs[:, 2:4] = np.exp(outs[:, 2:4]) * self.yolox_strides

        # Extraer teléfonos celulares (COCO clase 67 = cell phone exclusivamente) con umbral firme
        sens = float(self.moderation_rules.get("sensitivity", 0.78))
        phone_thresh = max(0.42, 0.58 - sens * 0.16)
        phone_scores = outs[:, 4] * outs[:, 5 + 67]
        p_mask = phone_scores > phone_thresh
        phones_found = []
        if np.any(p_mask):
            p_boxes = outs[p_mask, :4] / scale
            p_sc = phone_scores[p_mask]
            p_xywh = []
            valid_sc = []
            for (cx, cy, bw, bh), sc_val in zip(p_boxes, p_sc):
                x = int(max(0, cx - bw / 2))
                y = int(max(0, cy - bh / 2))
                bw_i = int(min(w - x, bw))
                bh_i = int(min(h - y, bh))
                ar = bw_i / max(1.0, float(bh_i))
                if 14 <= bw_i <= w * 0.35 and 14 <= bh_i <= h * 0.35 and 0.35 <= ar <= 2.4:
                    p_xywh.append([x, y, bw_i, bh_i])
                    valid_sc.append(float(sc_val))
            if p_xywh:
                p_idx = cv2.dnn.NMSBoxes(p_xywh, valid_sc, phone_thresh, 0.40)
                if len(p_idx) > 0:
                    for i in np.array(p_idx).flatten():
                        phones_found.append((tuple(p_xywh[i]), float(valid_sc[i])))
        self.last_yolox_phones = phones_found

        scores = outs[:, 4] * outs[:, 5]
        mask = scores > conf_thresh
        if not np.any(mask):
            return []

        det_boxes = outs[mask, :4] / scale
        det_scores = scores[mask]

        bboxes_xywh = []
        for (cx, cy, bw, bh) in det_boxes:
            x = int(max(0, cx - bw / 2))
            y = int(max(0, cy - bh / 2))
            bw_i = int(min(w - x, bw))
            bh_i = int(min(h - y, bh))
            bboxes_xywh.append([x, y, bw_i, bh_i])

        indices = cv2.dnn.NMSBoxes(bboxes_xywh, det_scores.tolist(), conf_thresh, 0.45)
        results = []
        if len(indices) > 0:
            for idx in np.array(indices).flatten():
                results.append((tuple(bboxes_xywh[idx]), float(det_scores[idx])))
        return results

    def _detect_cellphones_in_roi(
        self,
        roi: np.ndarray,
        rx0: int,
        ry0: int,
        w_img: int,
        h_img: int,
    ) -> List[Tuple[Tuple[int, int, int, int], float]]:
        """
        Ejecuta detección enfocada (Zoom ROI) sobre el torso/manos/rostro de la persona
        para atrapar teléfonos celulares pequeños o de perfil tomando fotos/video.
        """
        if self.person_net is None or roi is None or roi.size == 0:
            return []

        rh, rw = roi.shape[:2]
        ih, iw = self.yolox_size
        scale = min(ih / rh, iw / rw)
        nh, nw = int(rh * scale), int(rw * scale)

        resized = cv2.resize(roi, (nw, nh), interpolation=cv2.INTER_LINEAR)
        padded = np.full((ih, iw, 3), 114, dtype=np.uint8)
        padded[:nh, :nw] = resized

        blob = cv2.dnn.blobFromImage(padded, 1.0, self.yolox_size, (0, 0, 0), swapRB=False)
        self.person_net.setInput(blob)
        outs = self.person_net.forward()[0].copy()

        outs[:, :2] = (outs[:, :2] + self.yolox_grids) * self.yolox_strides
        outs[:, 2:4] = np.exp(outs[:, 2:4]) * self.yolox_strides

        sens = float(self.moderation_rules.get("sensitivity", 0.78))
        phone_thresh = max(0.45, 0.62 - sens * 0.16)
        # Exclusivamente Clase 67 = cell phone (sin 'remote' para evitar confundir mouse/objetos de escritorio)
        scores = outs[:, 4] * outs[:, 5 + 67]
        mask = scores > phone_thresh
        if not np.any(mask):
            return []

        det_boxes = outs[mask, :4] / scale
        det_scores = scores[mask]
        bboxes_xywh = []
        valid_scores = []
        for (cx, cy, bw, bh), sc_val in zip(det_boxes, det_scores):
            x = int(max(0, cx - bw / 2 + rx0))
            y = int(max(0, cy - bh / 2 + ry0))
            bw_i = int(min(w_img - x, bw))
            bh_i = int(min(h_img - y, bh))
            ar = bw_i / max(1.0, float(bh_i))
            # Filtrar tamaños y proporciones de un smartphone real
            if 16 <= bw_i <= rw * 0.48 and 16 <= bh_i <= rh * 0.48 and 0.35 <= ar <= 2.4:
                bboxes_xywh.append([x, y, bw_i, bh_i])
                valid_scores.append(float(sc_val))

        if not bboxes_xywh:
            return []

        indices = cv2.dnn.NMSBoxes(bboxes_xywh, valid_scores, phone_thresh, 0.40)
        results = []
        if len(indices) > 0:
            for idx in np.array(indices).flatten():
                results.append((tuple(bboxes_xywh[idx]), float(valid_scores[idx])))
        return results

    def _detect_faces_yunet(self, frame: np.ndarray) -> List[np.ndarray]:
        if self.face_detector is None:
            return []
        h, w = frame.shape[:2]
        max_w = 640
        scale = 1.0
        if w > max_w:
            scale = max_w / w
            small = cv2.resize(frame, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_LINEAR)
        else:
            small = frame

        sh, sw = small.shape[:2]
        self.face_detector.setInputSize((sw, sh))
        _, faces = self.face_detector.detect(small)
        if faces is None:
            return []

        if scale != 1.0:
            faces = faces.copy()
            faces[:, :14] /= scale
        return [f for f in faces]

    # -------------------------------------------------------------------------
    # DETECCIÓN ESTRICTA DE SEÑAS Y ACTOS OFENSIVOS CONFIRMADOS (CERO SOSPECHAS)
    # -------------------------------------------------------------------------
    def _detect_hands_and_gestures(
        self,
        frame: np.ndarray,
        analyses: List[PersonAnalysis],
    ) -> List[HandGestureInfo]:
        """
        Analiza exclusivamente manos reales confirmadas con sus 21 articulaciones 3D.
        Elimina cualquier detector por 'sospecha' de color de piel para que NUNCA bluree
        si el acto no se está efectuando realmente.
        """
        if not analyses:
            if self.moderation_rules.get("block_cellphone_use", True) and self.last_yolox_phones:
                return [
                    HandGestureInfo(
                        bbox=pb,
                        landmarks=np.zeros((21, 3), dtype=np.float32),
                        gesture_name="📸 ACTO INDEBIDO: Tomando Fotos / Grabando con Celular",
                        is_offensive=True,
                        is_wave_pose=False,
                        confidence=psc,
                        category="cellphone_photo",
                    )
                    for (pb, psc) in self.last_yolox_phones[:2]
                ]
            return []

        h_img, w_img = frame.shape[:2]
        hand_infos: List[HandGestureInfo] = []

        # 1. Detectar palmas sobre el cuadro completo (mantiene la escala de anclas exacta de MediaPipe)
        if self.palm_detector is not None and self.handpose_detector is not None:
            try:
                f_scale = 1.0
                if max(h_img, w_img) > 760:
                    f_scale = 760.0 / max(h_img, w_img)
                    f_infer = cv2.resize(frame, (int(w_img * f_scale), int(h_img * f_scale)), interpolation=cv2.INTER_LINEAR)
                else:
                    f_infer = frame

                full_palms = self.palm_detector.infer(f_infer)
                if full_palms is not None and len(full_palms) > 0:
                    for palm in full_palms[:4]:
                        hand_res = self.handpose_detector.infer(f_infer, palm)
                        if hand_res is None:
                            continue
                        conf = float(hand_res[131])
                        if conf < 0.60:
                            continue

                        hb = hand_res[0:4] / f_scale
                        lms = hand_res[4:67].reshape(21, 3).copy()
                        lms[:, 0] /= f_scale
                        lms[:, 1] /= f_scale

                        hx0 = int(max(0, hb[0]))
                        hy0 = int(max(0, hb[1]))
                        hx1 = int(min(w_img, hb[2]))
                        hy1 = int(min(h_img, hb[3]))
                        h_bbox = (hx0, hy0, max(14, hx1 - hx0), max(14, hy1 - hy0))
                        hcx, hcy = hx0 + (hx1 - hx0) * 0.5, hy0 + (hy1 - hy0) * 0.5

                        # Asociar con la persona más cercana
                        best_pa = analyses[0]
                        best_d = 1e9
                        for pa_cand in analyses:
                            px, py, pw, ph = pa_cand.bbox
                            d = math.hypot(hcx - (px + pw * 0.5), hcy - (py + ph * 0.45))
                            if d < best_d:
                                best_d = d
                                best_pa = pa_cand

                        gesture_name, is_off, is_wave, cat = self._classify_confirmed_hand_act(
                            lms, h_bbox, best_pa
                        )
                        hand_infos.append(
                            HandGestureInfo(
                                bbox=h_bbox,
                                landmarks=lms,
                                gesture_name=gesture_name,
                                is_offensive=is_off,
                                is_wave_pose=is_wave,
                                confidence=conf,
                                category=cat,
                            )
                        )
            except Exception:
                pass

        for pa in analyses[:3]:
            px, py, pw, ph = pa.bbox
            rx0 = max(0, int(px - pw * 0.35))
            ry0 = max(0, int(py - ph * 0.15))
            rx1 = min(w_img, int(px + pw * 1.35))
            ry1 = min(h_img, int(py + ph * 1.25))
            if rx1 - rx0 < 40 or ry1 - ry0 < 40:
                continue

            roi = frame[ry0:ry1, rx0:rx1]
            rh, rw = roi.shape[:2]

            # Solo buscar manos en ROI si no se detectó ya una mano en el cuadro completo para esta persona
            has_hand_for_pa = any(
                (rx0 <= (hi.bbox[0] + hi.bbox[2] * 0.5) <= rx1) and (ry0 <= (hi.bbox[1] + hi.bbox[3] * 0.5) <= ry1)
                for hi in hand_infos
            )
            if not has_hand_for_pa and self.palm_detector is not None and self.handpose_detector is not None:
                scale = 1.0
                if max(rh, rw) > 460:
                    scale = 460.0 / max(rh, rw)
                    roi_infer = cv2.resize(roi, (int(rw * scale), int(rh * scale)), interpolation=cv2.INTER_LINEAR)
                else:
                    roi_infer = roi

                try:
                    palms = self.palm_detector.infer(roi_infer)
                    if palms is not None and len(palms) > 0:
                        for palm in palms[:2]:
                            hand_res = self.handpose_detector.infer(roi_infer, palm)
                            if hand_res is None:
                                continue

                            conf = float(hand_res[131])
                            if conf < 0.60:
                                continue

                            hb = hand_res[0:4] / scale
                            lms = hand_res[4:67].reshape(21, 3).copy()
                            lms[:, 0] = lms[:, 0] / scale + rx0
                            lms[:, 1] = lms[:, 1] / scale + ry0

                            hx0 = int(max(0, hb[0] + rx0))
                            hy0 = int(max(0, hb[1] + ry0))
                            hx1 = int(min(w_img, hb[2] + rx0))
                            hy1 = int(min(h_img, hb[3] + ry0))
                            h_bbox = (hx0, hy0, max(14, hx1 - hx0), max(14, hy1 - hy0))

                            gesture_name, is_off, is_wave, cat = self._classify_confirmed_hand_act(
                                lms, h_bbox, pa
                            )
                            hand_infos.append(
                                HandGestureInfo(
                                    bbox=h_bbox,
                                    landmarks=lms,
                                    gesture_name=gesture_name,
                                    is_offensive=is_off,
                                    is_wave_pose=is_wave,
                                    confidence=conf,
                                    category=cat,
                                )
                            )
                except Exception:
                    pass

            # -----------------------------------------------------------------
            # DETECCIÓN DE TELÉFONO CELULAR / TOMANDO FOTOGRAFÍAS O GRABANDO
            # (Exige que haya una mano real sosteniendo el dispositivo o confianza alta junto al rostro)
            # -----------------------------------------------------------------
            if self.moderation_rules.get("block_cellphone_use", True):
                person_phones = []
                for (pb, psc) in self.last_yolox_phones:
                    pcx = pb[0] + pb[2] * 0.5
                    pcy = pb[1] + pb[3] * 0.5
                    if rx0 <= pcx <= rx1 and ry0 <= pcy <= ry1:
                        person_phones.append((pb, psc))

                if not person_phones and hand_infos:
                    person_phones = self._detect_cellphones_in_roi(roi, rx0, ry0, w_img, h_img)

                for (pb, psc) in person_phones[:2]:
                    ph_x, ph_y, ph_w, ph_h = pb
                    ph_cx = ph_x + ph_w * 0.5
                    ph_cy = ph_y + ph_h * 0.5

                    # Verificar que haya una mano cerca sosteniendo el celular o esté junto al rostro con alta confianza
                    held_by_hand = False
                    for hi in hand_infos:
                        hx, hy, hw, hh = hi.bbox
                        hcx, hcy = hx + hw * 0.5, hy + hh * 0.5
                        if math.hypot(ph_cx - hcx, ph_cy - hcy) <= max(hw, hh, ph_w, ph_h) * 1.45:
                            held_by_hand = True
                            break

                    near_face_high_conf = False
                    if pa.has_face and pa.face_bbox is not None and psc >= 0.58:
                        fx, fy, fw, fh = pa.face_bbox
                        if math.hypot(ph_cx - (fx + fw * 0.5), ph_cy - (fy + fh * 0.5)) <= max(fw, fh) * 1.8:
                            near_face_high_conf = True

                    if not (held_by_hand or near_face_high_conf):
                        continue

                    is_taking_photo = False
                    if pa.has_face and pa.face_bbox is not None:
                        fx, fy, fw, fh = pa.face_bbox
                        fcx = fx + fw * 0.5
                        if ph_cy <= (fy + fh * 2.35) and (abs(ph_cx - fcx) < fw * 1.15 or ph_w > ph_h * 1.15):
                            is_taking_photo = True
                    else:
                        if ph_cy <= (py + ph * 0.54) or ph_w > ph_h * 1.15:
                            is_taking_photo = True

                    if is_taking_photo:
                        phone_label = "📸 ACTO CONFIRMADO: Tomando Fotos / Grabando con Celular"
                        phone_cat = "cellphone_photo"
                    else:
                        phone_label = "📱 ACTO CONFIRMADO: Uso de Teléfono Celular"
                        phone_cat = "cellphone_use"

                    hand_infos.append(
                        HandGestureInfo(
                            bbox=pb,
                            landmarks=np.zeros((21, 3), dtype=np.float32),
                            gesture_name=phone_label,
                            is_offensive=True,
                            is_wave_pose=False,
                            confidence=psc,
                            category=phone_cat,
                        )
                    )

        return hand_infos

    def _classify_confirmed_hand_act(
        self,
        lms: np.ndarray,
        h_bbox: Tuple[int, int, int, int],
        pa: PersonAnalysis,
    ) -> Tuple[str, bool, bool, str]:
        """
        FASE 1 (PRE-ANÁLISIS GEOMÉTRICO 3D):
        Detecta señas ofensivas tanto de frente como de perfil/dorso (incluyendo dedo único
        levantado sobre puño cerrado) mientras descarta manos tecleando abajo en el escritorio.
        """
        rules = self.moderation_rules
        px, py, pw, ph = pa.bbox
        hx, hy, hw, hh = h_bbox
        hcx, hcy = hx + hw * 0.5, hy + hh * 0.5

        near_face = False
        if pa.has_face and pa.face_bbox is not None:
            fx, fy, fw, fh = pa.face_bbox
            if (fx - fw * 0.8 <= hcx <= fx + fw * 1.8) and (fy - fh * 0.5 <= hcy <= fy + fh * 2.2):
                near_face = True

        # Solo se considera zona de teclado/escritorio si NO está cerca del rostro/mentón y está muy abajo en un cuerpo alto
        in_desk_typing_zone = (not near_face) and (ph > 260) and (hcy > py + ph * 0.86)

        wrist = lms[0, :2]
        palm_center = np.mean(lms[[0, 5, 9, 13, 17], :2], axis=0)
        palm_size = max(14.0, float(np.linalg.norm(lms[9, :2] - wrist)))

        def finger_metrics(mcp_id, pip_id, dip_id, tip_id):
            d_tip_w = float(np.linalg.norm(lms[tip_id, :2] - wrist))
            d_pip_w = float(np.linalg.norm(lms[pip_id, :2] - wrist))
            d_mcp_w = float(np.linalg.norm(lms[mcp_id, :2] - wrist))
            d_tip_palm = float(np.linalg.norm(lms[tip_id, :2] - palm_center))
            ext = (d_tip_w - d_pip_w) / palm_size
            ratio = d_tip_w / max(1.0, d_mcp_w)
            v1 = lms[pip_id, :2] - lms[mcp_id, :2]
            v2 = lms[tip_id, :2] - lms[pip_id, :2]
            cos_ang = np.dot(v1, v2) / max(1e-4, np.linalg.norm(v1) * np.linalg.norm(v2))
            return ext, ratio, float(cos_ang), d_tip_palm / palm_size

        idx_ext, idx_rat, idx_cos, idx_palm = finger_metrics(5, 6, 7, 8)
        mid_ext, mid_rat, mid_cos, mid_palm = finger_metrics(9, 10, 11, 12)
        rng_ext, rng_rat, rng_cos, rng_palm = finger_metrics(13, 14, 15, 16)
        pnk_ext, pnk_rat, pnk_cos, pnk_palm = finger_metrics(17, 18, 19, 20)

        thumb_tip_d = float(np.linalg.norm(lms[4, :2] - palm_center)) / palm_size
        thumb_ip_d = float(np.linalg.norm(lms[3, :2] - palm_center)) / palm_size
        thumb_open = thumb_tip_d > 1.00 and (thumb_tip_d > thumb_ip_d * 1.12)

        idx_open = idx_ext > 0.14 and idx_rat > 1.28 and idx_cos > 0.62
        mid_open = mid_ext > 0.14 and mid_rat > 1.28 and mid_cos > 0.62
        rng_open = rng_ext > 0.13 and rng_rat > 1.25 and rng_cos > 0.58
        pnk_open = pnk_ext > 0.12 and pnk_rat > 1.22 and pnk_cos > 0.55

        # Un dedo está cerrado/doblado si su punta se curva hacia adentro (ext < 0.05 y cos_ang bajo/negativo)
        idx_folded = (idx_ext < 0.05 and idx_cos < 0.55) or (idx_ext < -0.05)
        mid_folded = (mid_ext < 0.05 and mid_cos < 0.55) or (mid_ext < -0.05)
        rng_folded = (rng_ext < 0.05 and rng_cos < 0.55) or (rng_ext < -0.05)
        pnk_folded = (pnk_ext < 0.05 and pnk_cos < 0.55) or (pnk_ext < -0.05)

        if not in_desk_typing_zone:
            # -----------------------------------------------------------------
            # 1. CANDIDATO: DEDO MEDIO ("BRITNEY SEÑAL" FRONTAL, DIAGONAL O DE DORSO/PERFIL)
            # -----------------------------------------------------------------
            if rules.get("block_middle_finger", True):
                # Caso A: Vista frontal/diagonal estándar (dedo 12 extendido y 8, 16, 20 doblados)
                case_a_mid = (
                    mid_open
                    and idx_folded
                    and rng_folded
                    and pnk_folded
                    and (mid_ext - idx_ext > 0.18)
                    and (mid_ext - rng_ext > 0.18)
                )
                # Caso B: Vista de dorso/perfil oblicuo donde un solo dedo sobresale verticalmente hacia arriba
                # sobre el puño cerrado (los otros 4 dedos tienen ext < -0.15 y cos < -0.40)
                four_tightly_curled = (
                    idx_ext < -0.15 and mid_ext < -0.15 and rng_ext < -0.15 and pnk_ext < -0.15
                    and idx_cos < -0.40 and mid_cos < -0.40 and rng_cos < -0.40
                )
                min_fist_y = float(np.min(lms[5:, 1]))
                single_finger_protruding_up = (
                    four_tightly_curled
                    and (min_fist_y - lms[4, 1] > palm_size * 0.55)
                    and (lms[0, 1] - lms[4, 1] > palm_size * 1.45)
                )
                if case_a_mid or single_finger_protruding_up:
                    return "🚫 ACTO CONFIRMADO: Seña Dedo Medio", True, False, "middle_finger"

            # -----------------------------------------------------------------
            # 2. CANDIDATO: SEÑA DE ARMA / PISTOLA APUNTANDO
            # -----------------------------------------------------------------
            if rules.get("block_gun_gesture", True):
                confirmed_gun = (
                    idx_open
                    and idx_cos > 0.78
                    and thumb_open
                    and mid_folded
                    and rng_folded
                    and pnk_folded
                    and (idx_ext - mid_ext > 0.24)
                )
                if confirmed_gun:
                    return "🚫 ACTO CONFIRMADO: Seña de Pistola / Arma", True, False, "gun_sign"

            # -----------------------------------------------------------------
            # 3. CANDIDATO: SEÑA DE CUERNOS / INSULTO ("CORNA")
            # -----------------------------------------------------------------
            if rules.get("block_horns_insult", True):
                confirmed_horns = (
                    idx_open
                    and pnk_open
                    and mid_folded
                    and rng_folded
                    and (idx_palm > mid_palm * 1.25)
                    and (pnk_palm > rng_palm * 1.25)
                )
                if confirmed_horns:
                    return "🚫 ACTO CONFIRMADO: Seña de Cuernos / Insulto", True, False, "horns_insult"

            # -----------------------------------------------------------------
            # 4. CANDIDATO: PULGAR ABAJO (RECHAZO / ABUCHEO)
            # -----------------------------------------------------------------
            if rules.get("block_thumbs_down", True):
                thumb_vertical_down = (lms[4, 1] - lms[2, 1] > palm_size * 0.48) and (abs(lms[4, 0] - lms[2, 0]) < palm_size * 0.38)
                confirmed_thumbs_down = (
                    thumb_open
                    and thumb_vertical_down
                    and idx_folded
                    and mid_folded
                    and rng_folded
                    and pnk_folded
                    and (lms[4, 1] > lms[5, 1] + palm_size * 0.30)
                )
                if confirmed_thumbs_down:
                    return "🚫 ACTO CONFIRMADO: Pulgar Abajo", True, False, "thumbs_down"

            # -----------------------------------------------------------------
            # 5. CANDIDATO OPCIONAL: PUÑO AMENAZANTE
            # -----------------------------------------------------------------
            if rules.get("block_fist_threat", False):
                all_folded = idx_folded and mid_folded and rng_folded and pnk_folded and (not thumb_open)
                if all_folded and (hy + hh * 0.5) < (py + ph * 0.35):
                    return "🚫 ACTO CONFIRMADO: Puño Amenazante", True, False, "fist_threat"

        # ---------------------------------------------------------------------
        # 6. SALUDO AMIGABLE (MANO ABIERTA)
        # ---------------------------------------------------------------------
        open_count = sum([idx_open, mid_open, rng_open, pnk_open])
        if open_count >= 3 and (lms[12, 1] < lms[0, 1]):
            return "Saludo / Mano Abierta 👋", False, True, "wave"

        return "Mano Normal", False, False, "normal"

    # -------------------------------------------------------------------------
    # FASE 2: POST-ANÁLISIS PROFUNDO DEL FRAME POR LA IA (VERIFICADOR ANTI-FALSOS POSITIVOS)
    # -------------------------------------------------------------------------
    def _post_analyze_frame_and_confirm(
        self,
        frame: np.ndarray,
        pa: PersonAnalysis,
        cand: HandGestureInfo,
    ) -> bool:
        """
        Post-Análisis IA del frame:
        Antes de declarar un acto como ofensivo (y antes de blurear o guardar evidencia),
        analiza el recorte del frame donde se detectó el candidato:
        1. Para celulares: re-verifica con YOLOX Clase 67 en recorte dedicado y exige presencia de mano/piel.
        2. Para señas de mano: exige certeza 3D >= 0.72, proporciones anatómicas coherentes con el cuerpo/rostro,
           que las 21 articulaciones caigan sobre tejido/piel real (no sobre monitores, muebles o ropa),
           y re-valida la geometría 3D completa del gesto.
        """
        h_img, w_img = frame.shape[:2]
        px, py, pw, ph = pa.bbox
        hx, hy, hw, hh = cand.bbox

        # 1. Post-Análisis para Teléfono Celular / Toma de Fotografías
        if cand.category in ("cellphone_photo", "cellphone_use"):
            pad = int(max(hw, hh) * 1.1)
            cx0, cy0 = max(0, hx - pad), max(0, hy - pad)
            cx1, cy1 = min(w_img, hx + hw + pad), min(h_img, hy + hh + pad)
            crop = frame[cy0:cy1, cx0:cx1]
            if crop.size == 0:
                return False

            # Verificar que en la región del teléfono haya piel/mano real sosteniéndolo
            ycrcb = cv2.cvtColor(crop, cv2.COLOR_BGR2YCrCb)
            skin_mask = cv2.inRange(ycrcb, np.array([35, 132, 75], dtype=np.uint8), np.array([255, 178, 132], dtype=np.uint8))
            skin_ratio = float(np.count_nonzero(skin_mask)) / float(max(1, crop.shape[0] * crop.shape[1]))
            if skin_ratio < 0.06:
                return False

            re_phones = self._detect_cellphones_in_roi(crop, cx0, cy0, w_img, h_img)
            if not re_phones and cand.confidence < 0.55:
                return False
            return True

        # 2. Post-Análisis para Señas de Mano Ofensivas
        if cand.confidence < 0.72:
            return False

        # Validar proporciones anatómicas coherentes de la mano respecto al cuerpo/rostro
        ar = hw / max(1.0, float(hh))
        if hw < 18 or hh < 18 or hw > pw * 0.88 or hh > ph * 0.88 or not (0.35 <= ar <= 2.4):
            return False

        if pa.has_face and pa.face_bbox is not None:
            _, _, fw, fh = pa.face_bbox
            face_sz = max(fw, fh)
            hand_sz = max(hw, hh)
            # Una mano real no puede ser minúscula (<0.32x rostro) ni gigante (>2.2x rostro)
            if hand_sz < face_sz * 0.32 or hand_sz > face_sz * 2.2:
                return False

        # Verificar en el frame que las 21 articulaciones caigan realmente sobre una mano humana
        # y no sobre un objeto inanimado del fondo (gabinete, monitor, silla, pizarrón)
        x0, y0 = max(0, hx), max(0, hy)
        x1, y1 = min(w_img, hx + hw), min(h_img, hy + hh)
        hand_crop = frame[y0:y1, x0:x1]
        if hand_crop.shape[0] < 16 or hand_crop.shape[1] < 16:
            return False

        ycrcb = cv2.cvtColor(hand_crop, cv2.COLOR_BGR2YCrCb)
        skin_mask = cv2.inRange(ycrcb, np.array([30, 130, 75], dtype=np.uint8), np.array([255, 180, 135], dtype=np.uint8))
        skin_area_ratio = float(np.count_nonzero(skin_mask)) / float(hand_crop.shape[0] * hand_crop.shape[1])

        lms = cand.landmarks
        skin_joints = 0
        for jx, jy, _ in lms:
            ix, iy = int(round(jx)), int(round(jy))
            if 0 <= ix < w_img and 0 <= iy < h_img:
                patch = frame[max(0, iy - 2) : min(h_img, iy + 3), max(0, ix - 2) : min(w_img, ix + 3)]
                if patch.size > 0:
                    p_ycrcb = cv2.cvtColor(patch, cv2.COLOR_BGR2YCrCb)
                    p_skin = cv2.inRange(p_ycrcb, np.array([30, 130, 75], dtype=np.uint8), np.array([255, 180, 135], dtype=np.uint8))
                    if np.count_nonzero(p_skin) > 0:
                        skin_joints += 1

        if skin_area_ratio < 0.15 or skin_joints < 9:
            return False

        # Re-evaluar geometría estricta 3D de las 21 articulaciones
        _, post_is_off, _, post_cat = self._classify_confirmed_hand_act(lms, cand.bbox, pa)
        return bool(post_is_off and post_cat == cand.category)

    # -------------------------------------------------------------------------
    # SUPER SCREENSHOT RÁPIDO DE EVIDENCIA DE MAL USO (GUARDADO ASÍNCRONO)
    # -------------------------------------------------------------------------
    def _save_evidence_screenshot_async(
        self,
        raw_frame: np.ndarray,
        pa: PersonAnalysis,
        hand_info: HandGestureInfo,
    ):
        """
        Toma un Super Screenshot instantáneo cuando se confirma un acto ofensivo y lo almacena
        en la carpeta `evidencias_mal_uso/` sin bloquear el video:
        - Compone una imagen de evidencia forense que incluye:
          1. La toma completa SIN BLUR (para tener la prueba clara del acto efectuado) con recuadro rojo en la mano e infractor.
          2. Un panel lateral con Zoom de alta definición al rostro, Zoom al gesto de la mano y la ficha biométrica completa + fecha/hora exacta.
        """
        now = time.time()
        last_ts = self.last_evidence_time.get(pa.person_id, 0.0)
        # Cooldown de 3.5 segundos por persona para no llenar el disco con el mismo gesto continuo
        if now - last_ts < 3.5:
            return
        self.last_evidence_time[pa.person_id] = now

        frame_copy = raw_frame.copy()
        p_copy = PersonAnalysis(
            person_id=pa.person_id,
            bbox=pa.bbox,
            confidence=pa.confidence,
            has_face=pa.has_face,
            face_bbox=pa.face_bbox,
            landmarks=None if pa.landmarks is None else pa.landmarks.copy(),
            gender=pa.gender,
            age=pa.age,
            age_range=pa.age_range,
            expression=pa.expression,
            upper_color=pa.upper_color,
            style_desc=pa.style_desc,
            economic_profile=pa.economic_profile,
            physical_desc=pa.physical_desc,
            accessories=pa.accessories,
            posture=pa.posture,
            is_offensive=True,
            offensive_reason=hand_info.gesture_name,
        )
        h_bbox = hand_info.bbox
        gesture_str = hand_info.gesture_name

        def _writer_task():
            try:
                ts_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
                file_ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
                h, w = frame_copy.shape[:2]

                # Crear lienzo de Super Screenshot de Evidencia (Imagen principal 1280x720 + Panel Forense Derecho 420x720)
                ev_w, ev_h = 1700, 720
                canvas = np.full((ev_h, ev_w, 3), (18, 20, 28), dtype=np.uint8)

                # Dibujar marcadores de evidencia sobre el frame limpio (sin blur)
                annotated = frame_copy.copy()
                px, py, pw, ph = p_copy.bbox
                hx, hy, hw, hh = h_bbox
                cv2.rectangle(annotated, (px, py), (px + pw, py + ph), (0, 140, 255), 3)
                cv2.rectangle(annotated, (hx, hy), (hx + hw, hy + hh), (0, 0, 255), 4)
                cv2.putText(
                    annotated,
                    f"EVIDENCIA: {gesture_str}",
                    (max(10, hx - 10), max(30, hy - 12)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.75,
                    (0, 0, 255),
                    2,
                    cv2.LINE_AA,
                )

                main_720 = cv2.resize(annotated, (1280, 720), interpolation=cv2.INTER_LINEAR)
                canvas[:, :1280] = main_720

                # Extraer recorte de acercamiento de la mano infractora (Zoom al acto)
                pad_h = int(max(hw, hh) * 0.65)
                gx0, gy0 = max(0, hx - pad_h), max(0, hy - pad_h)
                gx1, gy1 = min(w, hx + hw + pad_h), min(h, hy + hh + pad_h)
                hand_crop = frame_copy[gy0:gy1, gx0:gx1]
                if hand_crop.size > 0:
                    hand_thumb = cv2.resize(hand_crop, (185, 185), interpolation=cv2.INTER_CUBIC)
                    canvas[65:250, 1300:1485] = hand_thumb
                    cv2.rectangle(canvas, (1300, 65), (1485, 250), (0, 0, 255), 2)
                    cv2.putText(canvas, "ZOOM ACTO / MANO", (1305, 58), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (100, 100, 255), 1, cv2.LINE_AA)

                # Extraer recorte del rostro o parte superior del infractor
                if p_copy.has_face and p_copy.face_bbox is not None:
                    fx, fy, fw, fh = p_copy.face_bbox
                    pad_f = int(max(fw, fh) * 0.5)
                    fx0, fy0 = max(0, fx - pad_f), max(0, fy - pad_f)
                    fx1, fy1 = min(w, fx + fw + pad_f), min(h, fy + fh + pad_f)
                    face_crop = frame_copy[fy0:fy1, fx0:fx1]
                else:
                    face_crop = frame_copy[max(0, py) : min(h, py + int(ph * 0.45)), max(0, px) : min(w, px + pw)]

                if face_crop.size > 0:
                    face_thumb = cv2.resize(face_crop, (185, 185), interpolation=cv2.INTER_CUBIC)
                    canvas[65:250, 1500:1685] = face_thumb
                    cv2.rectangle(canvas, (1500, 65), (1685, 250), (56, 189, 248), 2)
                    cv2.putText(canvas, "ZOOM ROSTRO / SUJETO", (1502, 58), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (56, 189, 248), 1, cv2.LINE_AA)

                # Textos del Reporte Forense en el panel derecho
                cv2.putText(canvas, "REPORTE DE EVIDENCIA - MAL USO", (1300, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.62, (0, 80, 255), 2, cv2.LINE_AA)
                lines = [
                    f"Fecha/Hora: {ts_str}",
                    f"Camara: {self.cam_label}",
                    f"Acto Confirmado: {gesture_str.replace('🚫 ', '')}",
                    "----------------------------------------",
                    f"Sujeto ID: #{p_copy.person_id}",
                    f"Genero: {p_copy.gender}",
                    f"Edad Estimada: {p_copy.age_range}",
                    f"Estatus Visual: {p_copy.economic_profile[:32]}",
                    f"Vestimenta: {p_copy.upper_color}",
                    f"Estilo: {p_copy.style_desc[:34]}",
                    f"Accesorios: {p_copy.accessories}",
                    f"Postura: {p_copy.posture}",
                ]
                y_txt = 290
                for ln in lines:
                    color_t = (120, 120, 255) if "Acto Confirmado" in ln else (230, 235, 245)
                    cv2.putText(canvas, ln, (1300, y_txt), cv2.FONT_HERSHEY_SIMPLEX, 0.48, color_t, 1, cv2.LINE_AA)
                    y_txt += 32

                img_filename = f"evidencia_{self.cam_label}_{file_ts}.jpg"
                img_path = os.path.join(self.evidences_dir, img_filename)
                cv2.imwrite(img_path, canvas, [int(cv2.IMWRITE_JPEG_QUALITY), 95])

                # Guardar también registro en bitácora JSONL
                log_entry = {
                    "timestamp": ts_str,
                    "camera": self.cam_label,
                    "evidence_image": img_filename,
                    "confirmed_act": gesture_str,
                    "person_profile": self._person_to_dict(p_copy),
                }
                log_path = os.path.join(self.evidences_dir, "bitacora_evidencias.jsonl")
                with open(log_path, "a", encoding="utf-8") as f_log:
                    f_log.write(json.dumps(log_entry, ensure_ascii=False) + "\n")

                with self.state_lock:
                    self.total_evidences_saved += 1
                    self.last_evidence_file = img_filename
            except Exception as e:
                print(f"[AI Evidence] Error guardando evidencia: {e}")

        threading.Thread(target=_writer_task, daemon=True).start()

    def _analyze_person_profile(self, frame: np.ndarray, pa: PersonAnalysis, idx_key: int):
        h_img, w_img = frame.shape[:2]
        px, py, pw, ph = pa.bbox

        if pa.has_face and pa.face_bbox is not None:
            fx, fy, fw, fh = pa.face_bbox
            fcx, fcy = fx + fw // 2, fy + fh // 2
            rad = int(max(fw, fh) * 0.56)
            fx0, fy0 = max(0, fcx - rad), max(0, fcy - rad)
            fx1, fy1 = min(w_img, fcx + rad), min(h_img, fcy + rad)
            face_crop = frame[fy0:fy1, fx0:fx1]

            if face_crop.size > 0 and self.genderage_net is not None:
                try:
                    blob = cv2.dnn.blobFromImage(face_crop, 1.0, (96, 96), (0, 0, 0), swapRB=True)
                    self.genderage_net.setInput(blob)
                    out = self.genderage_net.forward()[0]
                    g_diff = float(out[1] - out[0])

                    hair_y0 = max(0, fy + int(fh * 0.55))
                    hair_y1 = min(h_img, fy + int(fh * 1.25))
                    hair_x0 = max(0, fx - int(fw * 0.25))
                    hair_x1 = min(w_img, fx + int(fw * 1.25))
                    hair_roi = frame[hair_y0:hair_y1, hair_x0:hair_x1]
                    long_hair_bonus = 0.0
                    if hair_roi.size > 0:
                        gray_h = cv2.cvtColor(hair_roi, cv2.COLOR_BGR2GRAY)
                        dark_ratio = float(np.count_nonzero(gray_h < 68)) / float(gray_h.size)
                        if dark_ratio > 0.38:
                            long_hair_bonus = -0.32

                    prev_g = self.gender_votes.get(idx_key, 0.0)
                    smoothed_g = prev_g * 0.65 + (g_diff + long_hair_bonus) * 0.35
                    self.gender_votes[idx_key] = smoothed_g
                    pa.gender = "Hombre" if smoothed_g >= 0.0 else "Mujer"

                    raw_age = max(16, min(78, int(out[2] * 100)))
                    prev_age = self.age_ema.get(idx_key, float(raw_age))
                    pa.age = int(round(prev_age * 0.7 + raw_age * 0.3))
                    self.age_ema[idx_key] = float(pa.age)

                    if pa.age < 25:
                        pa.age_range = f"~{pa.age} años (Joven 18-25)"
                    elif pa.age < 36:
                        pa.age_range = f"~{pa.age} años (Adulto Joven 26-35)"
                    elif pa.age < 50:
                        pa.age_range = f"~{pa.age} años (Adulto 36-50)"
                    else:
                        pa.age_range = f"~{pa.age} años (Senior 50+)"
                except Exception:
                    pass

            if pa.landmarks is not None:
                re, le, nt, rm, lm = pa.landmarks
                ex0 = int(max(0, min(re[0], le[0]) - fw * 0.15))
                ex1 = int(min(w_img, max(re[0], le[0]) + fw * 0.15))
                ey0 = int(max(0, min(re[1], le[1]) - fh * 0.14))
                ey1 = int(min(h_img, max(re[1], le[1]) + fh * 0.14))
                eye_strip = frame[ey0:ey1, ex0:ex1]
                if eye_strip.size > 0:
                    edges = cv2.Canny(eye_strip, 60, 150)
                    edge_density = float(np.count_nonzero(edges)) / float(edges.size)
                    pa.accessories = "Lentes / Gafas detectados" if edge_density > 0.13 else "Sin lentes visibles"

                mouth_w = math.hypot(lm[0] - rm[0], lm[1] - rm[1])
                eye_w = max(1.0, math.hypot(le[0] - re[0], le[1] - re[1]))
                smile_ratio = mouth_w / eye_w
                if smile_ratio > 0.92:
                    pa.expression = "Sonriente / Amigable 😊"
                elif pa.eye_contact:
                    pa.expression = "Atento / Mirando a Cámara 👁️"
                else:
                    pa.expression = "Concentrado / Neutral"
        else:
            pa.gender = "Persona (Rostro no visible)"
            pa.age_range = "No determinado (De espaldas/perfil)"

        tx0 = max(0, int(px + pw * 0.20))
        tx1 = min(w_img, int(px + pw * 0.80))
        ty0 = max(0, int(py + ph * 0.28))
        ty1 = min(h_img, int(py + ph * 0.68))
        torso_roi = frame[ty0:ty1, tx0:tx1]

        color_name, sat_mean, val_mean, contrast_std = self._classify_clothing_region(torso_roi)
        pa.upper_color = color_name

        aspect = pw / float(max(1, ph))
        if ph < h_img * 0.55 and (py + ph) > h_img * 0.75:
            pa.posture = "Sentado / En estación de trabajo"
        else:
            pa.posture = "De pie / En desplazamiento"

        build_str = "Complexión Media"
        if aspect > 0.68:
            build_str = "Complexión Robusta / Ancha"
        elif aspect < 0.42:
            build_str = "Complexión Delgada / Estilizada"

        if sat_mean < 48 and (60 < val_mean < 210) and contrast_std > 28:
            pa.style_desc = f"Formal / Business Casual (Prenda {color_name}, corte estructurado)"
            pa.economic_profile = "Medio-Alto / Ejecutivo Profesional (Índice Visual: Alto)"
        elif val_mean > 185 and sat_mean < 38:
            pa.style_desc = f"Smart Casual / Pulcro (Prenda clara {color_name})"
            pa.economic_profile = "Medio-Alto / Profesional (Índice Visual: Bueno)"
        elif sat_mean < 65:
            pa.style_desc = f"Casual Urbano / Oficina (Prenda {color_name})"
            pa.economic_profile = "Medio / Estándar Profesional"
        else:
            pa.style_desc = f"Casual / Deportivo (Tono vivo {color_name})"
            pa.economic_profile = "Medio / Casual Estándar"

        pa.physical_desc = f"{pa.gender}, {pa.age_range} | {build_str} | {pa.accessories}"

    def _classify_clothing_region(self, roi: np.ndarray) -> Tuple[str, float, float, float]:
        if roi is None or roi.size == 0:
            return "No visible", 0.0, 0.0, 0.0
        small = cv2.resize(roi, (24, 24))
        hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
        h_mean = float(np.median(hsv[:, :, 0]))
        s_mean = float(np.mean(hsv[:, :, 1]))
        v_mean = float(np.mean(hsv[:, :, 2]))
        v_std = float(np.std(hsv[:, :, 2]))

        if v_mean < 48:
            return "Negro / Oscuro", s_mean, v_mean, v_std
        if s_mean < 32:
            if v_mean > 190:
                return "Blanco", s_mean, v_mean, v_std
            return "Gris / Neutro", s_mean, v_mean, v_std

        if h_mean < 11 or h_mean > 168:
            cname = "Rojo / Vino"
        elif h_mean < 24:
            cname = "Naranja / Café Claro"
        elif h_mean < 36:
            cname = "Amarillo / Beige"
        elif h_mean < 82:
            cname = "Verde"
        elif h_mean < 132:
            cname = "Azul / Marino"
        else:
            cname = "Morado / Rosa"
        return cname, s_mean, v_mean, v_std

    def _evaluate_eye_contact(self, face_row: np.ndarray, idx_key: int) -> Tuple[bool, float]:
        re_x, re_y = face_row[0], face_row[1]
        le_x, le_y = face_row[2], face_row[3]
        nt_x, nt_y = face_row[4], face_row[5]
        rm_x, rm_y = face_row[6], face_row[7]
        lm_x, lm_y = face_row[8], face_row[9]
        conf = float(face_row[14])

        eye_dx = le_x - re_x
        eye_dy = le_y - re_y
        inter_ocular = math.hypot(eye_dx, eye_dy)
        if inter_ocular < 7.5:
            return False, 0.0

        eye_mid_x = (re_x + le_x) * 0.5
        eye_mid_y = (re_y + le_y) * 0.5
        yaw_asym = abs(nt_x - eye_mid_x) / inter_ocular
        roll_ratio = abs(eye_dy) / inter_ocular

        mouth_mid_y = (rm_y + lm_y) * 0.5
        face_vert = max(mouth_mid_y - eye_mid_y, 1.0)
        nose_pitch_ratio = (nt_y - eye_mid_y) / face_vert

        pitch_penalty = 0.0
        if nose_pitch_ratio < 0.28 or nose_pitch_ratio > 0.78:
            pitch_penalty = 0.42

        raw_score = max(0.0, 1.0 - (yaw_asym * 1.55) - (roll_ratio * 0.75) - pitch_penalty)
        raw_score = min(1.0, raw_score * min(1.0, conf + 0.15))

        prev = self.gaze_ema.get(idx_key, raw_score)
        gaze_score = prev * 0.65 + raw_score * 0.35
        self.gaze_ema[idx_key] = gaze_score

        is_looking = (yaw_asym < 0.25) and (roll_ratio < 0.30) and (0.28 <= nose_pitch_ratio <= 0.78) and (gaze_score >= 0.56)
        return is_looking, gaze_score

    def _evaluate_wave_and_motion(
        self,
        fg_mask: np.ndarray,
        person_bbox: Tuple[int, int, int, int],
        face_bbox: Optional[Tuple[int, int, int, int]],
        person_idx: int,
    ) -> Tuple[bool, float, float]:
        h_img, w_img = fg_mask.shape[:2]
        x, y, w, h = person_bbox
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(w_img, x + w), min(h_img, y + h)
        if x1 <= x0 or y1 <= y0:
            return False, 0.0, 0.0

        roi_mask = fg_mask[y0:y1, x0:x1]
        motion_level = float(np.count_nonzero(roi_mask)) / float(max(1, (x1 - x0) * (y1 - y0)))

        pad_x = int(w * 0.28)
        pad_top = int(h * 0.15)
        wx0 = max(0, x - pad_x)
        wy0 = max(0, y - pad_top)
        wx1 = min(w_img, x + w + pad_x)
        wy1 = min(h_img, y + int(h * 0.52))

        wave_mask = fg_mask[wy0:wy1, wx0:wx1].copy()

        if face_bbox is not None:
            fx, fy, fw, fh = face_bbox
            rfx0 = max(0, fx - wx0 - int(fw * 0.15))
            rfy0 = max(0, fy - wy0 - int(fh * 0.15))
            rfx1 = min(wave_mask.shape[1], fx + fw - wx0 + int(fw * 0.15))
            rfy1 = min(wave_mask.shape[0], fy + fh - wy0 + int(fh * 0.25))
            wave_mask[rfy0:rfy1, rfx0:rfx1] = 0

        contours, _ = cv2.findContours(wave_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        person_area = max(1.0, float(w * h))
        best_wave_score = 0.0
        hand_centroid_x = None

        for cnt in contours:
            area = cv2.contourArea(cnt)
            ratio = area / person_area
            if 0.012 <= ratio <= 0.25:
                M = cv2.moments(cnt)
                if M["m00"] > 0:
                    cx = M["m10"] / M["m00"]
                    cy = M["m01"] / M["m00"]
                    if cy < (wy1 - wy0) * 0.78:
                        hand_centroid_x = cx / max(1.0, float(wx1 - wx0))
                        best_wave_score = max(best_wave_score, min(1.0, ratio * 14.0))

        hist = self.wave_history.get(person_idx, [])
        now = time.time()
        if hand_centroid_x is not None:
            hist.append((now, hand_centroid_x))
        hist = [(t, pos) for (t, pos) in hist if now - t <= 1.8]
        self.wave_history[person_idx] = hist

        oscillation_bonus = 0.0
        if len(hist) >= 3:
            xs = [pos for _, pos in hist]
            span = max(xs) - min(xs)
            if span > 0.055:
                oscillation_bonus = min(0.6, span * 3.5)

        total_wave = min(1.0, best_wave_score * 0.65 + oscillation_bonus)
        is_waving = total_wave >= 0.46
        return is_waving, total_wave, motion_level

    # -------------------------------------------------------------------------
    # 1. MÉTODO ASÍNCRONO DE INFERENCIA
    # -------------------------------------------------------------------------
    def analyze_async(self, frame: np.ndarray):
        self.ai_cycle_count += 1
        h, w = frame.shape[:2]
        now = time.time()

        small_w, small_h = 320, int(320 * h / max(1, w))
        small_frame = cv2.resize(frame, (small_w, small_h), interpolation=cv2.INTER_NEAREST)
        fg_small = self.bg_subtractor.apply(small_frame)
        fg_mask = cv2.resize(fg_small, (w, h), interpolation=cv2.INTER_NEAREST)
        motion_ratio = float(np.count_nonzero(fg_small)) / float(small_w * small_h)

        if motion_ratio > 0.008:
            self.last_motion_time = now

        person_dets = self._detect_persons_yolox(frame)
        face_dets = self._detect_faces_yunet(frame)

        analyses: List[PersonAnalysis] = []
        used_faces = set()

        for p_idx, (p_bbox, p_conf) in enumerate(person_dets):
            px, py, pw, ph = p_bbox
            pa = PersonAnalysis(person_id=p_idx + 1, bbox=p_bbox, confidence=p_conf)

            for f_idx, f_row in enumerate(face_dets):
                if f_idx in used_faces:
                    continue
                fx, fy, fw, fh = int(f_row[0]), int(f_row[1]), int(f_row[2]), int(f_row[3])
                fcx, fcy = fx + fw * 0.5, fy + fh * 0.5
                if (px - pw * 0.15 <= fcx <= px + pw * 1.15) and (py - ph * 0.15 <= fcy <= py + ph * 0.65):
                    used_faces.add(f_idx)
                    pa.has_face = True
                    pa.face_bbox = (fx, fy, fw, fh)
                    pa.landmarks = f_row[4:14].reshape(5, 2)
                    pa.eye_contact, pa.gaze_score = self._evaluate_eye_contact(f_row, p_idx)
                    break

            pa.is_waving, pa.wave_score, pa.motion_level = self._evaluate_wave_and_motion(
                fg_mask, p_bbox, pa.face_bbox, p_idx
            )
            self._analyze_person_profile(frame, pa, p_idx)
            analyses.append(pa)

        for f_idx, f_row in enumerate(face_dets):
            if f_idx in used_faces:
                continue
            fx, fy, fw, fh = int(f_row[0]), int(f_row[1]), int(f_row[2]), int(f_row[3])
            est_w, est_h = int(fw * 2.8), int(fh * 4.2)
            est_x = max(0, int(fx + fw * 0.5 - est_w * 0.5))
            est_y = max(0, int(fy - fh * 0.3))
            est_bbox = (est_x, est_y, min(w - est_x, est_w), min(h - est_y, est_h))

            pa = PersonAnalysis(
                person_id=len(analyses) + 1,
                bbox=est_bbox,
                confidence=float(f_row[14]),
                has_face=True,
                face_bbox=(fx, fy, fw, fh),
                landmarks=f_row[4:14].reshape(5, 2),
            )
            pa.eye_contact, pa.gaze_score = self._evaluate_eye_contact(f_row, 100 + f_idx)
            pa.is_waving, pa.wave_score, pa.motion_level = self._evaluate_wave_and_motion(
                fg_mask, est_bbox, (fx, fy, fw, fh), 100 + f_idx
            )
            self._analyze_person_profile(frame, pa, 100 + f_idx)
            analyses.append(pa)

        # Fase 1: Detectar candidatos preliminares
        hands = self._detect_hands_and_gestures(frame, analyses)
        hold_secs = float(self.moderation_rules.get("hold_seconds", 0.85))

        for pa in analyses:
            px, py, pw, ph = pa.bbox
            saw_candidate_this_cycle = False

            for h_info in hands:
                hx, hy, hw, hh = h_info.bbox
                hcx, hcy = hx + hw * 0.5, hy + hh * 0.5
                if (px - pw * 0.40 <= hcx <= px + pw * 1.40) and (py - ph * 0.30 <= hcy <= py + ph * 1.05):
                    if h_info.is_offensive:
                        saw_candidate_this_cycle = True
                        # Por defecto NO es ofensivo hasta que el Post-Análisis IA del frame lo confirme
                        h_info.is_offensive = False

                        # Ejecutar Post-Análisis IA sobre el frame nativo 1:1 para confirmar que el acto es real
                        if self._post_analyze_frame_and_confirm(frame, pa, h_info):
                            h_info.is_offensive = True
                            pa.is_offensive = True
                            pa.offensive_reason = h_info.gesture_name
                            pa.hand_boxes.append(h_info.bbox)
                            self.offensive_hold[pa.person_id] = (now + hold_secs, h_info.gesture_name, list(pa.hand_boxes))
                            # ¡Confirmado por Pre-Análisis y Post-Análisis IA! Guardar Super Screenshot
                            self._save_evidence_screenshot_async(frame, pa, h_info)

                    elif h_info.is_wave_pose:
                        pa.is_waving = True

            if not saw_candidate_this_cycle:
                prev_track = self.pre_analysis_tracker.get(pa.person_id)
                if prev_track is not None and (now - prev_track["last_ts"]) > 0.9:
                    self.pre_analysis_tracker.pop(pa.person_id, None)

            held = self.offensive_hold.get(pa.person_id)
            if held is not None and held[0] > now:
                pa.is_offensive = True
                if not pa.offensive_reason:
                    pa.offensive_reason = held[1]
                if not pa.hand_boxes:
                    pa.hand_boxes = held[2]

            tags = [f"{pa.gender} ({pa.age}a)" if pa.age > 0 else pa.gender]
            if pa.is_offensive:
                tags.append(f"{pa.offensive_reason}")
            elif pa.eye_contact:
                tags.append("CONTACTO VISUAL")
            if pa.is_waving and not pa.is_offensive:
                tags.append("SALUDANDO")
            pa.label = " | ".join(tags)

        # Desactivar cualquier candidato suelto que no haya sido verificado por Post-Análisis
        for h_info in hands:
            if h_info.is_offensive and not any(pa.is_offensive for pa in analyses):
                h_info.is_offensive = False

        with self.state_lock:
            self.last_analyses = analyses
            self.last_hands = hands
            self.global_motion_ratio = motion_ratio

    # -------------------------------------------------------------------------
    # 2. MÉTODO ULTRA RÁPIDO DE RENDERIZADO (<4 ms)
    # -------------------------------------------------------------------------
    def render_fast(
        self,
        frame: np.ndarray,
        enable_ai_overlay: bool = True,
        enable_auto_framing: bool = True,
        max_digital_zoom: float = 2.1,
        smooth_omega: float = 1.25,
        enhance_quality: bool = True,
        obstruction_blur: bool = True,
        enable_offensive_blur: bool = True,
        blur_full_person_on_offense: bool = False,
        enable_idle_patrol: bool = True,
        target_size: Tuple[int, int] = (1280, 720),
    ) -> Tuple[np.ndarray, dict]:
        h, w = frame.shape[:2]
        now = time.time()

        with self.state_lock:
            all_persons = list(self.last_analyses)
            hands = list(self.last_hands)
            motion_ratio = self.global_motion_ratio
            ev_count = self.total_evidences_saved
            last_ev = self.last_evidence_file

        engaged = [p for p in all_persons if (p.eye_contact or p.is_waving) and not p.is_offensive]
        offensive_count = sum(1 for p in all_persons if p.is_offensive)
        time_without_motion = now - self.last_motion_time

        if engaged:
            self.last_attention_time = now
            self.is_engaged_active = True
            self.is_patrolling = False
            tx, ty, tz = self._compute_target_from_boxes(
                [self._focus_box_for_engaged(p, w, h) for p in engaged],
                w,
                h,
                max_zoom=max_digital_zoom,
                margin=1.55,
            )
            self.spring.set_target(tx, ty, tz)
            self.active_mode_banner = "🎯 CONEXIÓN 1 A 1 ACTIVA (CONTACTO VISUAL / SALUDO)"

        elif (now - self.last_attention_time) < self.attention_hold_seconds and all_persons:
            self.is_engaged_active = True
            self.is_patrolling = False

        elif enable_idle_patrol and (time_without_motion >= self.idle_before_patrol_seconds or not all_persons):
            self.is_engaged_active = False
            self.is_patrolling = True
            patrol_zoom = 1.22
            half_span = 0.5 / patrol_zoom
            max_pan = max(0.04, (0.5 - half_span) * 0.85)
            pan_x = 0.5 + max_pan * math.sin(now * 0.11)
            self.spring.set_target(pan_x, 0.5, patrol_zoom, deadzone_pos=0.0005, deadzone_zoom=0.005)
            dir_str = "DERECHA ➡️" if math.cos(now * 0.11) >= 0 else "⬅️ IZQUIERDA"
            self.active_mode_banner = f"🔄 PATRULLA OSCILANTE ULTRA SUAVE ({dir_str})"

        else:
            self.is_engaged_active = False
            self.is_patrolling = False
            self.spring.set_target(0.5, 0.5, 1.0)
            self.active_mode_banner = f"🌐 CAMPO ABIERTO PANORÁMICO ({len(all_persons)} persona(s) en escena)"

        if not enable_auto_framing and not self.is_patrolling:
            self.spring.set_target(0.5, 0.5, 1.0, deadzone_pos=0.0, deadzone_zoom=0.0)

        cur_cx, cur_cy, cur_zoom = self.spring.step(omega=smooth_omega)

        output = frame.copy()

        # Solo blurea cuando hay un acto ofensivo 100% confirmado ejecutándose
        if enable_offensive_blur and (offensive_count > 0 or any(h_i.is_offensive for h_i in hands)):
            self._apply_offensive_moderation_blur(output, all_persons, hands, blur_full_person_on_offense, show_text=enable_ai_overlay)

        if obstruction_blur and all_persons:
            largest_area = max((p.bbox[2] * p.bbox[3]) / float(w * h) for p in all_persons)
            if largest_area > 0.68:
                small_b = cv2.resize(output, (80, 45), interpolation=cv2.INTER_LINEAR)
                output = cv2.resize(small_b, (w, h), interpolation=cv2.INTER_LINEAR)
                if enable_ai_overlay:
                    cv2.putText(
                        output,
                        "PROTECCION DE LENTE ACTIVA (MANTENGA DISTANCIA)",
                        (int(w * 0.12), int(h * 0.5)),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.85,
                        (255, 255, 255),
                        2,
                        cv2.LINE_AA,
                    )

        if enable_ai_overlay:
            self._draw_overlays(output, all_persons, hands)

        tw, th = target_size
        if cur_zoom > 1.003:
            # Interpolación sub-píxel continua por matriz afín (CERO escalonamiento de píxeles al moverse lento)
            crop_w_f = float(w) / float(cur_zoom)
            crop_h_f = float(h) / float(cur_zoom)
            x0_f = max(0.0, min(float(w) - crop_w_f, float(cur_cx) * float(w) - crop_w_f * 0.5))
            y0_f = max(0.0, min(float(h) - crop_h_f, float(cur_cy) * float(h) - crop_h_f * 0.5))
            sx = float(tw) / crop_w_f
            sy = float(th) / crop_h_f
            M = np.array(
                [
                    [sx, 0.0, -x0_f * sx],
                    [0.0, sy, -y0_f * sy],
                ],
                dtype=np.float32,
            )
            output_720p = cv2.warpAffine(
                output,
                M,
                (tw, th),
                flags=cv2.INTER_LINEAR,
                borderMode=cv2.BORDER_REPLICATE,
            )
        else:
            crop_w_f = float(w)
            crop_h_f = float(h)
            x0_f = 0.0
            y0_f = 0.0
            if w != tw or h != th:
                output_720p = cv2.resize(output, (tw, th), interpolation=cv2.INTER_LINEAR)
            else:
                output_720p = output

        # Transformar extremidades/manos detectadas y gesto de Pellizco (Pulgar #4 + Índice #8) a coordenadas normalizadas [0..1]
        tracked_hands = []
        for h_info in hands:
            if h_info.is_offensive:
                continue
            lms = h_info.landmarks
            if lms is not None and lms.shape[0] >= 21 and np.any(lms):
                palm_sz = max(14.0, math.hypot(float(lms[9, 0] - lms[0, 0]), float(lms[9, 1] - lms[0, 1])))
                pinch_d = math.hypot(float(lms[4, 0] - lms[8, 0]), float(lms[4, 1] - lms[8, 1]))
                pinch_ratio = pinch_d / palm_sz
                raw_cx = (float(lms[4, 0]) + float(lms[8, 0])) * 0.5
                raw_cy = (float(lms[4, 1]) + float(lms[8, 1])) * 0.5
                norm_cx = max(0.0, min(1.0, (raw_cx - x0_f) / max(1.0, crop_w_f)))
                norm_cy = max(0.0, min(1.0, (raw_cy - y0_f) / max(1.0, crop_h_f)))
                lms_norm = [
                    (
                        max(0.0, min(1.0, (float(pt[0]) - x0_f) / max(1.0, crop_w_f))),
                        max(0.0, min(1.0, (float(pt[1]) - y0_f) / max(1.0, crop_h_f))),
                    )
                    for pt in lms
                ]
                tracked_hands.append(
                    {
                        "cx": norm_cx,
                        "cy": norm_cy,
                        "pinch_ratio": pinch_ratio,
                        "is_pinching": bool(pinch_ratio < 0.62),
                        "landmarks_norm": lms_norm,
                    }
                )
            else:
                hx, hy, hw, hh = h_info.bbox
                raw_cx = hx + hw * 0.5
                raw_cy = hy + hh * 0.5
                norm_cx = max(0.0, min(1.0, (raw_cx - x0_f) / max(1.0, crop_w_f)))
                norm_cy = max(0.0, min(1.0, (raw_cy - y0_f) / max(1.0, crop_h_f)))
                tracked_hands.append(
                    {
                        "cx": norm_cx,
                        "cy": norm_cy,
                        "pinch_ratio": 1.0,
                        "is_pinching": False,
                        "landmarks_norm": [],
                    }
                )

        stats = {
            "persons": len(all_persons),
            "eye_contact": sum(1 for p in all_persons if p.eye_contact),
            "waving": sum(1 for p in all_persons if p.is_waving),
            "offensive": offensive_count,
            "evidences_saved": ev_count,
            "last_evidence": last_ev,
            "zoom": cur_zoom,
            "motion_pct": motion_ratio * 100.0,
            "mode": self.active_mode_banner,
            "engaged": self.is_engaged_active,
            "profiles": [self._person_to_dict(p) for p in all_persons],
            "tracked_hands": tracked_hands,
        }
        return output_720p, stats

    def process_frame(self, frame: np.ndarray, **kwargs) -> Tuple[np.ndarray, dict]:
        self.analyze_async(frame)
        return self.render_fast(frame, **kwargs)

    def _apply_offensive_moderation_blur(
        self,
        img: np.ndarray,
        persons: List[PersonAnalysis],
        hands: List[HandGestureInfo],
        blur_full_person: bool,
        show_text: bool = False,
    ):
        h_img, w_img = img.shape[:2]

        def blur_region(rx, ry, rw, rh, pad_factor=0.55, label_str="ACTO OFENSIVO CENSURADO"):
            px0 = max(0, int(rx - rw * pad_factor))
            py0 = max(0, int(ry - rh * pad_factor))
            px1 = min(w_img, int(rx + rw * (1.0 + pad_factor)))
            py1 = min(h_img, int(ry + rh * (1.0 + pad_factor)))
            if px1 > px0 and py1 > py0:
                roi = img[py0:py1, px0:px1]
                small = cv2.resize(roi, (max(4, (px1 - px0) // 16), max(4, (py1 - py0) // 16)), interpolation=cv2.INTER_LINEAR)
                pixelated = cv2.resize(small, (px1 - px0, py1 - py0), interpolation=cv2.INTER_NEAREST)
                img[py0:py1, px0:px1] = cv2.GaussianBlur(pixelated, (17, 17), 0)
                if show_text:
                    cv2.rectangle(img, (px0, py0), (px1, py1), (0, 0, 255), 2)
                    cv2.putText(
                        img,
                        label_str,
                        (px0 + 4, max(20, py0 - 6)),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.50,
                        (0, 0, 255),
                        2,
                        cv2.LINE_AA,
                    )

        for h_info in hands:
            if h_info.is_offensive:
                hx, hy, hw, hh = h_info.bbox
                blur_region(hx, hy, hw, hh, pad_factor=0.55, label_str=h_info.gesture_name)

        for p in persons:
            if p.is_offensive:
                if blur_full_person or not p.hand_boxes:
                    bx, by, bw, bh = p.bbox
                    blur_region(bx, by, bw, bh, pad_factor=0.05, label_str=p.offensive_reason or "ACTO OFENSIVO CENSURADO")
                else:
                    for (hx, hy, hw, hh) in p.hand_boxes:
                        blur_region(hx, hy, hw, hh, pad_factor=0.55, label_str=p.offensive_reason)

    def _person_to_dict(self, p: PersonAnalysis) -> dict:
        return {
            "id": p.person_id,
            "gender": p.gender,
            "age": p.age,
            "age_range": p.age_range,
            "expression": p.expression,
            "upper_color": p.upper_color,
            "style_desc": p.style_desc,
            "economic_profile": p.economic_profile,
            "physical_desc": p.physical_desc,
            "accessories": p.accessories,
            "posture": p.posture,
            "eye_contact": "Sí (Mirando a cámara)" if p.eye_contact else f"No ({int(p.gaze_score * 100)}%)",
            "waving": "Sí 👋" if p.is_waving else "No",
            "offensive": f"🚫 SÍ ({p.offensive_reason})" if p.is_offensive else "Ninguno (Seguro)",
        }

    def _focus_box_for_engaged(self, p: PersonAnalysis, w: int, h: int) -> Tuple[int, int, int, int]:
        px, py, pw, ph = p.bbox
        if p.has_face and p.face_bbox is not None:
            fx, fy, fw, fh = p.face_bbox
            cx = fx + fw * 0.5
            cy = fy + fh * 0.90
            box_w = max(fw * 3.8, pw * 1.05)
            box_h = max(fh * 4.2, ph * 0.78)
            x0 = int(max(0, cx - box_w * 0.5))
            y0 = int(max(0, cy - box_h * 0.45))
            return (x0, y0, int(min(w - x0, box_w)), int(min(h - y0, box_h)))
        return (px, py, pw, int(ph * 0.80))

    def _compute_target_from_boxes(
        self,
        boxes: List[Tuple[int, int, int, int]],
        w: int,
        h: int,
        max_zoom: float = 2.1,
        margin: float = 1.5,
    ) -> Tuple[float, float, float]:
        x0 = min(b[0] for b in boxes)
        y0 = min(b[1] for b in boxes)
        x1 = max(b[0] + b[2] for b in boxes)
        y1 = max(b[1] + b[3] for b in boxes)

        bw = max(1.0, float(x1 - x0) * margin)
        bh = max(1.0, float(y1 - y0) * margin)
        cx = (x0 + x1) * 0.5 / w
        cy = (y0 + y1) * 0.5 / h

        zoom_x = w / bw
        zoom_y = h / bh
        desired_zoom = max(1.0, min(max_zoom, min(zoom_x, zoom_y)))

        half_span = 0.5 / desired_zoom
        cx = max(half_span, min(1.0 - half_span, cx))
        cy = max(half_span, min(1.0 - half_span, cy))
        return cx, cy, desired_zoom

    def _draw_overlays(self, img: np.ndarray, analyses: List[PersonAnalysis], hands: List[HandGestureInfo]):
        for p in analyses:
            x, y, w, h = p.bbox
            if p.is_offensive:
                color = (0, 0, 255)
                thickness = 3
            elif p.eye_contact or p.is_waving:
                color = (0, 230, 118)
                thickness = 3
            elif p.motion_level > 0.08:
                color = (0, 191, 255)
                thickness = 2
            else:
                color = (235, 168, 52)
                thickness = 2

            cv2.rectangle(img, (x, y), (x + w, y + h), color, thickness)

            if p.has_face and p.face_bbox is not None and not p.is_offensive:
                fx, fy, fw, fh = p.face_bbox
                f_color = (50, 255, 120) if p.eye_contact else (0, 215, 255)
                cv2.rectangle(img, (fx, fy), (fx + fw, fy + fh), f_color, 2)
                if p.landmarks is not None:
                    for lx, ly in p.landmarks.astype(int):
                        cv2.circle(img, (lx, ly), 3, f_color, -1)

            label_txt = f"#{p.person_id} {p.label}"
            if p.has_face and not p.is_offensive:
                label_txt += f" | Mirada {int(p.gaze_score * 100)}%"
            (tw, th), _ = cv2.getTextSize(label_txt, cv2.FONT_HERSHEY_SIMPLEX, 0.52, 2)
            ly0 = max(th + 8, y)
            cv2.rectangle(img, (x, ly0 - th - 8), (x + tw + 12, ly0), color, -1)
            cv2.putText(
                img,
                label_txt,
                (x + 6, ly0 - 5),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.52,
                (15, 18, 24) if not p.is_offensive else (255, 255, 255),
                2,
                cv2.LINE_AA,
            )

        for h_info in hands:
            if not h_info.is_offensive:
                hx, hy, hw, hh = h_info.bbox
                cv2.rectangle(img, (hx, hy), (hx + hw, hy + hh), (255, 200, 0), 1)
                if np.any(h_info.landmarks):
                    for lx, ly, _ in h_info.landmarks.astype(int):
                        cv2.circle(img, (lx, ly), 2, (0, 255, 255), -1)
