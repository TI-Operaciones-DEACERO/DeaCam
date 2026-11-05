import os
import sys
import time
import json
import socket
import struct
import ctypes
import threading
from datetime import datetime
import tkinter as tk
from tkinter import ttk, messagebox

import cv2
import numpy as np
from PIL import Image, ImageTk
import requests
from requests.auth import HTTPDigestAuth

from ai_engine import SmartCameraAI, PersonAnalysis, HandGestureInfo
from kinect_games import KinectGameEngine
from gemini_verifier import GeminiPointVerifier
from p2p_portal import (
    UniversalCameraGrabber,
    AudioStreamEngine,
    PerpetualP2PNode,
    discover_usb_cameras,
    get_local_lan_ips,
)

# Opciones ultra-rápidas de FFmpeg para CERO retraso (Zero-Latency RTSP)
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = (
    "rtsp_transport;tcp|fflags;nobuffer|flags;low_delay|max_delay;0|stimeout;2500000"
)

# Alias compatible con cualquier cámara (Dahua RTSP, USB Local o RTSP/HTTP Universal)
LatestFrameGrabber = UniversalCameraGrabber


class DahuaPortalViewerApp:
    def __init__(self, root: tk.Tk, kiosk_mode: bool = False):
        self.root = root
        self.kiosk_mode = kiosk_mode or ("--kiosk" in sys.argv) or ("--portal-24-7" in sys.argv)
        self.root.title("DeaCam Portal IA [PANEL ADMIN] — Control de Llamada Perpetua 24/7, Moderación y Movimientos")
        self.root.geometry("1540x880")
        self.root.minsize(1280, 740)
        self.root.configure(bg="#0f1117")

        # Estado de captura Zero-Lag
        self.running = False
        self.grabber1: LatestFrameGrabber = None
        self.grabber2: LatestFrameGrabber = None

        self.frame_cam1 = None
        self.frame_cam2 = None
        self.stats_cam1 = {
            "persons": 0,
            "eye_contact": 0,
            "waving": 0,
            "offensive": 0,
            "zoom": 1.0,
            "motion_pct": 0.0,
            "mode": "CONECTANDO CAM 1...",
            "engaged": False,
            "profiles": [],
        }
        self.stats_cam2 = {
            "persons": 0,
            "eye_contact": 0,
            "waving": 0,
            "offensive": 0,
            "zoom": 1.0,
            "motion_pct": 0.0,
            "mode": "CONECTANDO CAM 2...",
            "engaged": False,
            "profiles": [],
        }
        self.lock_cam1 = threading.Lock()
        self.lock_cam2 = threading.Lock()
        self.ui_busy = False

        self.cam2_blend = 0.0

        self.processed_frame = None
        self.video_writer = None
        self.is_recording = False
        self.record_filename = ""
        self.frame_count = 0
        self.last_fps_time = time.time()
        self.actual_fps = 0.0
        self.last_profile_update = 0.0
        self.last_profile_signature = ""
        self.separate_profile_win = None
        self.separate_profile_txt = None
        self.catalog_win = None
        self.p2p_config_win = None
        self.portal_call_win = None
        self.portal_call_lbl = None
        self.portal_call_fullscreen = False
        self._loading_config = False

        base_dir = os.path.dirname(os.path.abspath(__file__))
        self.captures_dir = os.path.join(base_dir, "capturas")
        self.evidences_dir = os.path.join(base_dir, "evidencias_mal_uso")
        self.models_dir = os.path.join(base_dir, "models")
        self.config_path = os.path.join(base_dir, "config_portal.json")
        os.makedirs(self.captures_dir, exist_ok=True)
        os.makedirs(self.evidences_dir, exist_ok=True)
        os.makedirs(self.models_dir, exist_ok=True)

        # Motores de IA desacoplados con carpeta de evidencias de mal uso
        self.ai_cam1 = SmartCameraAI(self.models_dir, evidences_dir=self.evidences_dir, cam_label="Cam1_Panoramica")
        self.ai_cam2 = SmartCameraAI(self.models_dir, evidences_dir=self.evidences_dir, cam_label="Cam2_Vista1a1")

        # Motor de Juegos Interactivos Tipo Kinect AR (Detección de Extremidades y Gesto de Pellizco)
        self.kinect_engine = KinectGameEngine()

        # Copiloto Interpretativo Puntual con Gemini API (Ultra-Bajo Consumo de Tokens)
        self.gemini_verifier = GeminiPointVerifier()

        # Motor de Llamada Perpetua 24/7 en Red LAN P2P + Audio (Micrófono y Parlante)
        self.p2p_node = PerpetualP2PNode()
        self.audio_inputs_list, self.audio_outputs_list = AudioStreamEngine.list_audio_devices()
        self.usb_cameras_list = [{"index": 0, "label": "USB 0: Cámara Local / Webcam Predeterminada"}]

        # --- Variables de Preferencias del Panel de Control ---
        self.auto_save_prefs_var = tk.BooleanVar(value=True)

        # --- Variables de Cámara Universal y Cámaras Dahua ---
        self.camera_source_mode_var = tk.StringVar(value="Dahua Dual / Simple PoE (RTSP Automático)")
        self.usb_cam1_var = tk.StringVar(value="USB 0: Cámara Local / Webcam Predeterminada")
        self.usb_cam2_var = tk.StringVar(value="Desactivada (Solo 1 Cámara USB)")
        self.custom_rtsp1_var = tk.StringVar(value="rtsp://admin:SomosUnaFamilia2025.@192.168.1.109:554/cam/realmonitor?channel=1&subtype=0")
        self.custom_rtsp2_var = tk.StringVar(value="rtsp://admin:SomosUnaFamilia2025.@192.168.1.108:554/cam/realmonitor?channel=1&subtype=0")

        # --- Variables Cámara 1: Panorámica (Campo Abierto) ---
        self.ip1_var = tk.StringVar(value="192.168.1.109")
        self.user1_var = tk.StringVar(value="admin")
        self.pass1_var = tk.StringVar(value="SomosUnaFamilia2025.")
        self.mac1_var = tk.StringVar(value="98-f9-cc-95-db-bd")

        # --- Variables Cámara 2: Secundaria (Vista 1 a 1) ---
        self.enable_cam2_var = tk.BooleanVar(value=True)
        self.ip2_var = tk.StringVar(value="192.168.1.108")
        self.user2_var = tk.StringVar(value="admin")
        self.pass2_var = tk.StringVar(value="SomosUnaFamilia2025.")
        self.mac2_var = tk.StringVar(value="98-f9-cc-95-db-ec")

        self.stream_quality_var = tk.StringVar(value="0 (Principal HD - Con Drenado Zero-Lag)")

        # --- Variables de Llamada Perpetua 24/7 P2P, Micrófono y Parlante ---
        self.auto_join_call_var = tk.BooleanVar(value=True)
        self.auto_open_portal_win_var = tk.BooleanVar(value=True)
        self.target_peer_ip_var = tk.StringVar(value="Auto-Descubrimiento LAN P2P (24/7)")
        self.portal_call_view_mode_var = tk.StringVar(
            value="Automático 24/7 (Muestra Par Remoto en Llamada / Espejo Local en Espera)"
        )
        self.portal_call_pip_self_var = tk.BooleanVar(value=False)
        self.mic_device_var = tk.StringVar(value=self.audio_inputs_list[0]["label"])
        self.speaker_device_var = tk.StringVar(value=self.audio_outputs_list[0]["label"])
        self.enable_mic_var = tk.BooleanVar(value=True)
        self.enable_speaker_var = tk.BooleanVar(value=True)
        self.speaker_volume_var = tk.DoubleVar(value=1.0)
        self.mic_gain_var = tk.DoubleVar(value=1.0)
        self.p2p_status_var = tk.StringVar(value="🌐 Red P2P 24/7: Buscando equipos en la misma red...")

        # --- Variables de Modo de Señal, Ocultación de PiP, Moderación y Patrulla ---
        self.signal_mode_var = tk.StringVar(value="Auto-Director IA (Conmuta Panorámica <-> 1 a 1)")
        self.hide_inactive_cam_pip_var = tk.BooleanVar(value=True)
        self.mirror_mode_var = tk.BooleanVar(value=True)
        self.circular_portal_var = tk.BooleanVar(value=False)
        self.obstruction_blur_var = tk.BooleanVar(value=True)
        self.offensive_blur_var = tk.BooleanVar(value=True)
        self.blur_full_person_var = tk.BooleanVar(value=False)
        self.idle_patrol_var = tk.BooleanVar(value=True)
        self.ai_enabled_var = tk.BooleanVar(value=True)
        self.auto_frame_var = tk.BooleanVar(value=True)
        self.show_overlays_var = tk.BooleanVar(value=False)
        self.enhance_quality_var = tk.BooleanVar(value=True)
        # Por defecto: Zoom cómodo (1.65x) y Movimiento/Oscilación Ultra Suave y Lento (0.45)
        self.max_zoom_var = tk.DoubleVar(value=1.65)
        self.smooth_omega_var = tk.DoubleVar(value=0.45)

        # --- Variables de Juegos Tipo Kinect AR y Gesto de Pellizco ---
        self.enable_kinect_games_hud_var = tk.BooleanVar(value=True)
        self.kinect_play_mode_var = tk.StringVar(value="Automático (Solitario / Multijugador / Llamada P2P)")
        self.pinch_sensitivity_var = tk.DoubleVar(value=0.62)

        # --- Variables de IA Interpretativa Puntual con Gemini API (Ahorro de Tokens) ---
        self.gemini_enabled_var = tk.BooleanVar(value=False)
        self.gemini_api_key_var = tk.StringVar(value=os.environ.get("GEMINI_API_KEY", ""))
        self.gemini_model_var = tk.StringVar(value="gemini-2.5-flash (Recomendado - Ultra Económico)")
        self.gemini_trigger_offense_var = tk.BooleanVar(value=True)
        self.gemini_trigger_1to1_var = tk.BooleanVar(value=True)
        self.gemini_cooldown_var = tk.DoubleVar(value=30.0)
        self.gemini_max_calls_hr_var = tk.IntVar(value=30)
        self.gemini_usage_var = tk.StringVar(value="Llamadas: 0/30 hr | Tokens: ~0 ($0.00000 USD)")

        # --- Variables del Catálogo de Actos Confirmados y Evidencia ---
        self.mod_sensitivity_var = tk.DoubleVar(value=0.75)
        self.mod_hold_seconds_var = tk.DoubleVar(value=0.85)
        self.auto_evidence_var = tk.BooleanVar(value=True)
        self.rule_middle_finger_var = tk.BooleanVar(value=True)
        self.rule_gun_gesture_var = tk.BooleanVar(value=True)
        self.rule_fist_threat_var = tk.BooleanVar(value=False)
        self.rule_horns_insult_var = tk.BooleanVar(value=True)
        self.rule_thumbs_down_var = tk.BooleanVar(value=True)
        self.rule_cellphone_use_var = tk.BooleanVar(value=True)
        self.rule_pelvic_gesture_var = tk.BooleanVar(value=False)
        self.rule_face_mockery_var = tk.BooleanVar(value=False)
        self.rule_nudity_skin_var = tk.BooleanVar(value=False)

        # Cargar configuración recordada de config_portal.json antes de enlazar traces
        self._load_persistent_config()

        for v in (
            self.mod_sensitivity_var,
            self.mod_hold_seconds_var,
            self.auto_evidence_var,
            self.rule_middle_finger_var,
            self.rule_gun_gesture_var,
            self.rule_fist_threat_var,
            self.rule_horns_insult_var,
            self.rule_thumbs_down_var,
            self.rule_cellphone_use_var,
            self.rule_pelvic_gesture_var,
            self.rule_face_mockery_var,
            self.rule_nudity_skin_var,
        ):
            v.trace_add("write", lambda *args: self._sync_moderation_rules())

        for v in (
            self.auto_save_prefs_var,
            self.auto_join_call_var,
            self.auto_open_portal_win_var,
            self.target_peer_ip_var,
            self.portal_call_view_mode_var,
            self.portal_call_pip_self_var,
            self.camera_source_mode_var,
            self.usb_cam1_var,
            self.usb_cam2_var,
            self.custom_rtsp1_var,
            self.custom_rtsp2_var,
            self.ip1_var,
            self.user1_var,
            self.pass1_var,
            self.enable_cam2_var,
            self.ip2_var,
            self.user2_var,
            self.pass2_var,
            self.stream_quality_var,
            self.mic_device_var,
            self.speaker_device_var,
            self.enable_mic_var,
            self.enable_speaker_var,
            self.speaker_volume_var,
            self.mic_gain_var,
            self.signal_mode_var,
            self.hide_inactive_cam_pip_var,
            self.mirror_mode_var,
            self.circular_portal_var,
            self.obstruction_blur_var,
            self.offensive_blur_var,
            self.blur_full_person_var,
            self.idle_patrol_var,
            self.auto_frame_var,
            self.show_overlays_var,
            self.max_zoom_var,
            self.smooth_omega_var,
            self.enable_kinect_games_hud_var,
            self.kinect_play_mode_var,
            self.pinch_sensitivity_var,
            self.gemini_enabled_var,
            self.gemini_api_key_var,
            self.gemini_model_var,
            self.gemini_trigger_offense_var,
            self.gemini_trigger_1to1_var,
            self.gemini_cooldown_var,
            self.gemini_max_calls_hr_var,
        ):
            v.trace_add("write", lambda *args: self._on_any_setting_changed())

        self._sync_moderation_rules()
        self._sync_p2p_and_audio_settings()
        self._sync_kinect_settings()
        self._sync_gemini_settings()

        # Estados UI
        self.status_var = tk.StringVar(value="Portal 24/7 Listo — Preferencias del Panel Admin, Juegos Kinect y Red P2P Cargadas")
        self.net_status_var = tk.StringVar(value="Verificando cámaras y red P2P...")
        self.ai_banner_var = tk.StringVar(value="🌐 SEÑAL EN VIVO: CAMPO ABIERTO PANORÁMICO")
        self.ai_telemetry_var = tk.StringVar(value="Personas: 0 | Mirada: 0 | Saludos: 0 | Actos Confirmados: 0 | Evidencias: 0")

        self._setup_styles()
        self._build_ui()

        # Iniciar motor P2P 24/7 en segundo plano
        self.p2p_node.start()
        self._schedule_p2p_status_refresh()

        threading.Thread(target=self._discover_and_diagnose_cameras, daemon=True).start()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        # Si está activado "Auto-Unir a Llamada Perpetua 24/7", arrancar señal y portal limpio automáticamente
        if self.auto_join_call_var.get():
            self.root.after(650, self._auto_start_perpetual_portal)

    def _sync_gemini_settings(self):
        try:
            max_hr = int(self.gemini_max_calls_hr_var.get())
        except Exception:
            max_hr = 30
        self.gemini_verifier.configure(
            api_key=self.gemini_api_key_var.get(),
            model_name=self.gemini_model_var.get(),
            enabled=self.gemini_enabled_var.get(),
            trigger_offense=self.gemini_trigger_offense_var.get(),
            trigger_1to1=self.gemini_trigger_1to1_var.get(),
            cooldown_sec=float(self.gemini_cooldown_var.get()),
            max_per_hour=max_hr,
        )
        self.gemini_usage_var.set(self.gemini_verifier.get_usage_summary())

    def trigger_manual_gemini_analysis(self):
        """Realiza 1 consulta puntual bajo demanda a Gemini con el cuadro actual comprimido (384px)."""
        self._sync_gemini_settings()
        raw1, _, ok1 = self.grabber1.get_latest() if self.grabber1 else (None, 0, False)
        raw2, _, ok2 = self.grabber2.get_latest() if self.grabber2 else (None, 0, False)
        frame_src = raw2 if (ok2 and raw2 is not None and self.cam2_blend > 0.5) else (raw1 if (ok1 and raw1 is not None) else self.processed_frame)
        if frame_src is None:
            self.status_var.set("⚠️ Inicia la señal primero para realizar una consulta puntual con Gemini.")
            return

        self.status_var.set("✨ Enviando recorte puntual compacto (384px) a Gemini Vision API...")

        def _on_done(res: dict):
            self.last_profile_signature = ""
            self.root.after(
                0,
                lambda: [
                    self.gemini_usage_var.set(self.gemini_verifier.get_usage_summary()),
                    self.status_var.set(
                        f"✨ Dictamen Gemini ({res.get('timestamp')}): {res.get('context_summary')}"
                    ),
                ],
            )

        self.gemini_verifier.request_point_analysis_async(
            frame_src,
            trigger_reason="Consulta Puntual Manual (Operador)",
            force_manual=True,
            on_complete=_on_done,
        )

    def _sync_kinect_settings(self):
        self.kinect_engine.hud_enabled = bool(self.enable_kinect_games_hud_var.get())
        self.kinect_engine.pinch_threshold = float(self.pinch_sensitivity_var.get())
        pm = self.kinect_play_mode_var.get()
        if pm.startswith("Solitario"):
            self.kinect_engine.play_mode = "solo"
        elif pm.startswith("Multijugador"):
            self.kinect_engine.play_mode = "multi"
        elif pm.startswith("Llamada P2P"):
            self.kinect_engine.play_mode = "p2p"
        else:
            self.kinect_engine.play_mode = "auto"

    def _sync_moderation_rules(self):
        rules = {
            "sensitivity": float(self.mod_sensitivity_var.get()),
            "hold_seconds": float(self.mod_hold_seconds_var.get()),
            "auto_screenshot_evidence": bool(self.auto_evidence_var.get()),
            "block_middle_finger": bool(self.rule_middle_finger_var.get()),
            "block_gun_gesture": bool(self.rule_gun_gesture_var.get()),
            "block_fist_threat": bool(self.rule_fist_threat_var.get()),
            "block_horns_insult": bool(self.rule_horns_insult_var.get()),
            "block_thumbs_down": bool(self.rule_thumbs_down_var.get()),
            "block_cellphone_use": bool(self.rule_cellphone_use_var.get()),
            "block_pelvic_gesture": bool(self.rule_pelvic_gesture_var.get()),
            "block_face_mockery": bool(self.rule_face_mockery_var.get()),
            "block_nudity_skin": bool(self.rule_nudity_skin_var.get()),
        }
        self.ai_cam1.moderation_rules.update(rules)
        self.ai_cam2.moderation_rules.update(rules)
        if self.auto_save_prefs_var.get():
            self._save_persistent_config()

    def _on_any_setting_changed(self):
        if self._loading_config:
            return
        self._sync_p2p_and_audio_settings()
        self._sync_kinect_settings()
        self._sync_gemini_settings()
        if self.auto_save_prefs_var.get():
            self._save_persistent_config()

    def _resolve_audio_device_index(self, label_str: str, dev_list: list):
        for item in dev_list:
            if item["label"] == label_str:
                return item["index"]
        if label_str.startswith("[") and "]" in label_str:
            try:
                return int(label_str.split("]", 1)[0].strip("["))
            except Exception:
                pass
        return None

    def _sync_p2p_and_audio_settings(self):
        self.p2p_node.auto_join = bool(self.auto_join_call_var.get())
        peer_str = self.target_peer_ip_var.get().strip()
        if peer_str.lower().startswith("auto") or not peer_str:
            self.p2p_node.target_peer_ip = ""
        else:
            # Extraer IP si viene con formato "HOSTNAME (192.168.x.x)"
            if "(" in peer_str and ")" in peer_str:
                peer_str = peer_str.split("(", 1)[1].split(")", 1)[0].strip()
            self.p2p_node.target_peer_ip = peer_str

        ae = self.p2p_node.audio_engine
        ae.mic_muted = not bool(self.enable_mic_var.get())
        ae.speaker_muted = not bool(self.enable_speaker_var.get())
        ae.speaker_volume = float(self.speaker_volume_var.get())
        ae.mic_gain = float(self.mic_gain_var.get())

        in_idx = self._resolve_audio_device_index(self.mic_device_var.get(), self.audio_inputs_list)
        out_idx = self._resolve_audio_device_index(self.speaker_device_var.get(), self.audio_outputs_list)
        ae.configure_devices(in_idx, out_idx)

    def _load_persistent_config(self):
        """Carga automáticamente toda la configuración recordada del Panel Admin desde config_portal.json."""
        if not os.path.exists(self.config_path):
            return
        self._loading_config = True
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)

            str_map = {
                "camera_source_mode": self.camera_source_mode_var,
                "usb_cam1": self.usb_cam1_var,
                "usb_cam2": self.usb_cam2_var,
                "custom_rtsp1": self.custom_rtsp1_var,
                "custom_rtsp2": self.custom_rtsp2_var,
                "ip1": self.ip1_var,
                "user1": self.user1_var,
                "pass1": self.pass1_var,
                "mac1": self.mac1_var,
                "ip2": self.ip2_var,
                "user2": self.user2_var,
                "pass2": self.pass2_var,
                "mac2": self.mac2_var,
                "stream_quality": self.stream_quality_var,
                "target_peer_ip": self.target_peer_ip_var,
                "portal_call_view_mode": self.portal_call_view_mode_var,
                "mic_device": self.mic_device_var,
                "speaker_device": self.speaker_device_var,
                "signal_mode": self.signal_mode_var,
                "kinect_play_mode": self.kinect_play_mode_var,
                "gemini_api_key": self.gemini_api_key_var,
                "gemini_model": self.gemini_model_var,
            }
            for k, var in str_map.items():
                if k in cfg and cfg[k] is not None:
                    var.set(str(cfg[k]))

            bool_map = {
                "auto_save_prefs": self.auto_save_prefs_var,
                "auto_join_call": self.auto_join_call_var,
                "auto_open_portal_win": self.auto_open_portal_win_var,
                "portal_call_pip_self": self.portal_call_pip_self_var,
                "enable_cam2": self.enable_cam2_var,
                "enable_mic": self.enable_mic_var,
                "enable_speaker": self.enable_speaker_var,
                "hide_inactive_cam_pip": self.hide_inactive_cam_pip_var,
                "mirror_mode": self.mirror_mode_var,
                "circular_portal": self.circular_portal_var,
                "obstruction_blur": self.obstruction_blur_var,
                "offensive_blur": self.offensive_blur_var,
                "blur_full_person": self.blur_full_person_var,
                "idle_patrol": self.idle_patrol_var,
                "auto_frame": self.auto_frame_var,
                "show_overlays": self.show_overlays_var,
                "enable_kinect_games_hud": self.enable_kinect_games_hud_var,
                "gemini_enabled": self.gemini_enabled_var,
                "gemini_trigger_offense": self.gemini_trigger_offense_var,
                "gemini_trigger_1to1": self.gemini_trigger_1to1_var,
                "auto_evidence": self.auto_evidence_var,
                "rule_middle_finger": self.rule_middle_finger_var,
                "rule_gun_gesture": self.rule_gun_gesture_var,
                "rule_fist_threat": self.rule_fist_threat_var,
                "rule_horns_insult": self.rule_horns_insult_var,
                "rule_thumbs_down": self.rule_thumbs_down_var,
                "rule_cellphone_use": self.rule_cellphone_use_var,
                "rule_pelvic_gesture": self.rule_pelvic_gesture_var,
                "rule_face_mockery": self.rule_face_mockery_var,
                "rule_nudity_skin": self.rule_nudity_skin_var,
            }
            for k, var in bool_map.items():
                if k in cfg and cfg[k] is not None:
                    var.set(bool(cfg[k]))

            float_map = {
                "speaker_volume": self.speaker_volume_var,
                "mic_gain": self.mic_gain_var,
                "max_zoom": self.max_zoom_var,
                "smooth_omega": self.smooth_omega_var,
                "pinch_sensitivity": self.pinch_sensitivity_var,
                "gemini_cooldown": self.gemini_cooldown_var,
                "mod_sensitivity": self.mod_sensitivity_var,
                "mod_hold_seconds": self.mod_hold_seconds_var,
            }
            for k, var in float_map.items():
                if k in cfg and cfg[k] is not None:
                    var.set(float(cfg[k]))

            if "gemini_max_calls_hr" in cfg and cfg["gemini_max_calls_hr"] is not None:
                self.gemini_max_calls_hr_var.set(int(cfg["gemini_max_calls_hr"]))
        except Exception:
            pass
        finally:
            self._loading_config = False

    def _save_persistent_config(self):
        """Guarda automáticamente toda la configuración del Panel Admin en config_portal.json."""
        if self._loading_config:
            return
        try:
            cfg = {
                "auto_save_prefs": bool(self.auto_save_prefs_var.get()),
                "auto_join_call": bool(self.auto_join_call_var.get()),
                "auto_open_portal_win": bool(self.auto_open_portal_win_var.get()),
                "target_peer_ip": self.target_peer_ip_var.get(),
                "portal_call_view_mode": self.portal_call_view_mode_var.get(),
                "portal_call_pip_self": bool(self.portal_call_pip_self_var.get()),
                "camera_source_mode": self.camera_source_mode_var.get(),
                "usb_cam1": self.usb_cam1_var.get(),
                "usb_cam2": self.usb_cam2_var.get(),
                "custom_rtsp1": self.custom_rtsp1_var.get(),
                "custom_rtsp2": self.custom_rtsp2_var.get(),
                "ip1": self.ip1_var.get(),
                "user1": self.user1_var.get(),
                "pass1": self.pass1_var.get(),
                "mac1": self.mac1_var.get(),
                "enable_cam2": bool(self.enable_cam2_var.get()),
                "ip2": self.ip2_var.get(),
                "user2": self.user2_var.get(),
                "pass2": self.pass2_var.get(),
                "mac2": self.mac2_var.get(),
                "stream_quality": self.stream_quality_var.get(),
                "mic_device": self.mic_device_var.get(),
                "speaker_device": self.speaker_device_var.get(),
                "enable_mic": bool(self.enable_mic_var.get()),
                "enable_speaker": bool(self.enable_speaker_var.get()),
                "speaker_volume": float(self.speaker_volume_var.get()),
                "mic_gain": float(self.mic_gain_var.get()),
                "signal_mode": self.signal_mode_var.get(),
                "hide_inactive_cam_pip": bool(self.hide_inactive_cam_pip_var.get()),
                "mirror_mode": bool(self.mirror_mode_var.get()),
                "circular_portal": bool(self.circular_portal_var.get()),
                "obstruction_blur": bool(self.obstruction_blur_var.get()),
                "offensive_blur": bool(self.offensive_blur_var.get()),
                "blur_full_person": bool(self.blur_full_person_var.get()),
                "idle_patrol": bool(self.idle_patrol_var.get()),
                "auto_frame": bool(self.auto_frame_var.get()),
                "show_overlays": bool(self.show_overlays_var.get()),
                "max_zoom": float(self.max_zoom_var.get()),
                "smooth_omega": float(self.smooth_omega_var.get()),
                "enable_kinect_games_hud": bool(self.enable_kinect_games_hud_var.get()),
                "kinect_play_mode": self.kinect_play_mode_var.get(),
                "pinch_sensitivity": float(self.pinch_sensitivity_var.get()),
                "gemini_enabled": bool(self.gemini_enabled_var.get()),
                "gemini_api_key": self.gemini_api_key_var.get(),
                "gemini_model": self.gemini_model_var.get(),
                "gemini_trigger_offense": bool(self.gemini_trigger_offense_var.get()),
                "gemini_trigger_1to1": bool(self.gemini_trigger_1to1_var.get()),
                "gemini_cooldown": float(self.gemini_cooldown_var.get()),
                "gemini_max_calls_hr": int(self.gemini_max_calls_hr_var.get()),
                "mod_sensitivity": float(self.mod_sensitivity_var.get()),
                "mod_hold_seconds": float(self.mod_hold_seconds_var.get()),
                "auto_evidence": bool(self.auto_evidence_var.get()),
                "rule_middle_finger": bool(self.rule_middle_finger_var.get()),
                "rule_gun_gesture": bool(self.rule_gun_gesture_var.get()),
                "rule_fist_threat": bool(self.rule_fist_threat_var.get()),
                "rule_horns_insult": bool(self.rule_horns_insult_var.get()),
                "rule_thumbs_down": bool(self.rule_thumbs_down_var.get()),
                "rule_cellphone_use": bool(self.rule_cellphone_use_var.get()),
                "rule_pelvic_gesture": bool(self.rule_pelvic_gesture_var.get()),
                "rule_face_mockery": bool(self.rule_face_mockery_var.get()),
                "rule_nudity_skin": bool(self.rule_nudity_skin_var.get()),
            }
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    def save_preferences_manual(self):
        """Guarda explícitamente todas las preferencias del Panel de Control en config_portal.json con confirmación visual."""
        self._sync_moderation_rules()
        self._sync_p2p_and_audio_settings()
        self._sync_kinect_settings()
        self._sync_gemini_settings()
        self._save_persistent_config()
        ts_now = datetime.now().strftime("%H:%M:%S")
        self.status_var.set(f"💾 Preferencias del Panel de Control guardadas correctamente en config_portal.json ({ts_now})")
        if hasattr(self, "btn_save_prefs_top") and self.btn_save_prefs_top.winfo_exists():
            orig_txt = "💾 Guardar Preferencias"
            self.btn_save_prefs_top.configure(text="✅ ¡Preferencias Guardadas!", bg="#059669")
            self.root.after(
                2200,
                lambda: self.btn_save_prefs_top.configure(text=orig_txt, bg="#047857")
                if self.btn_save_prefs_top.winfo_exists()
                else None,
            )
        if hasattr(self, "btn_save_prefs_card") and self.btn_save_prefs_card.winfo_exists():
            orig_c = "💾 Guardar Preferencias del Panel Ahora"
            self.btn_save_prefs_card.configure(text="✅ ¡Guardado en config_portal.json!", bg="#059669")
            self.root.after(
                2200,
                lambda: self.btn_save_prefs_card.configure(text=orig_c, bg="#047857")
                if self.btn_save_prefs_card.winfo_exists()
                else None,
            )

    def reload_saved_preferences(self):
        """Recarga las preferencias almacenadas en config_portal.json."""
        self._load_persistent_config()
        self._sync_moderation_rules()
        self._sync_p2p_and_audio_settings()
        self._sync_kinect_settings()
        self._update_smooth_label(self.smooth_omega_var.get())
        self.zoom_lbl.configure(text=f"{self.max_zoom_var.get():.2f}x")
        self.status_var.set("📂 Preferencias recargadas desde config_portal.json")

    def reset_preferences_to_default(self):
        """Restaura las preferencias recomendadas por defecto del Panel de Control y las guarda."""
        self._loading_config = True
        try:
            self.signal_mode_var.set("Auto-Director IA (Conmuta Panorámica <-> 1 a 1)")
            self.hide_inactive_cam_pip_var.set(True)
            self.mirror_mode_var.set(True)
            self.offensive_blur_var.set(True)
            self.rule_cellphone_use_var.set(True)
            self.blur_full_person_var.set(False)
            self.idle_patrol_var.set(True)
            self.auto_frame_var.set(True)
            self.obstruction_blur_var.set(True)
            self.circular_portal_var.set(False)
            self.show_overlays_var.set(False)
            self.smooth_omega_var.set(0.45)
            self.max_zoom_var.set(1.65)
            self.mod_sensitivity_var.set(0.75)
            self.enable_kinect_games_hud_var.set(True)
            self.kinect_play_mode_var.set("Automático (Solitario / Multijugador / Llamada P2P)")
            self.pinch_sensitivity_var.set(0.62)
        finally:
            self._loading_config = False
        self._update_smooth_label(0.45)
        self.zoom_lbl.configure(text="1.65x")
        self.save_preferences_manual()
        self.status_var.set("🔄 Preferencias restauradas a los valores óptimos por defecto y guardadas.")

    def _auto_start_perpetual_portal(self):
        """
        Cuando el checkbox 'Auto-Unir a Llamada Perpetua 24/7' está marcado (o se inicia en modo Kiosco/Raspberry),
        inicia automáticamente la señal configurada, activa la red P2P + parlante y abre el Portal 24/7 en Fullscreen sin bordes.
        """
        if not self.running:
            self.start_unified_signal()
        if self.kiosk_mode or self.auto_open_portal_win_var.get():
            self.open_clean_portal_call_window(borderless_fullscreen=True)
            if self.kiosk_mode:
                self.root.withdraw()

    def enter_kiosk_portal_only_mode(self):
        """Oculta el Panel Admin y deja únicamente el Portal 24/7 en Fullscreen sin bordes."""
        self.kiosk_mode = True
        self.open_clean_portal_call_window(borderless_fullscreen=True)
        self.root.withdraw()

    def show_admin_panel(self, event=None):
        """
        Permite al Administrador (al conectar mouse o teclado en el Raspberry / PC)
        mostrar el Panel de Administrador pulsando F12, Esc, Ctrl+A o Clic Derecho.
        """
        self.root.deiconify()
        self.root.lift()
        try:
            self.root.focus_force()
        except Exception:
            pass
        if self.portal_call_win is not None and self.portal_call_win.winfo_exists():
            try:
                self.portal_call_win.config(cursor="arrow")
            except Exception:
                pass

    def _schedule_p2p_status_refresh(self):
        try:
            peers = self.p2p_node.get_peers_list()
            if self.p2p_node.in_call:
                dur = int(max(0.0, time.time() - self.p2p_node.call_connected_since))
                mins, secs = divmod(dur, 60)
                hrs, mins = divmod(mins, 60)
                self.p2p_status_var.set(
                    f"🟢 EN LLAMADA 24/7 CON: {self.p2p_node.active_peer_name} "
                    f"({hrs:02d}:{mins:02d}:{secs:02d} | {self.p2p_node.remote_fps:.0f} FPS)"
                )
            else:
                if peers:
                    names = ", ".join(f"{p['hostname']} ({p['ip']})" for p in peers[:2])
                    self.p2p_status_var.set(f"🟡 Equipos P2P en red ({len(peers)}): {names} — Conectando...")
                else:
                    lip = ", ".join(self.p2p_node.local_ips[:2])
                    self.p2p_status_var.set(
                        f"🌐 P2P 24/7 Activo (Mi IP: {lip}) — Esperando otro equipo en red..."
                    )
        except Exception:
            pass
        if self.root.winfo_exists():
            self.root.after(1200, self._schedule_p2p_status_refresh)

    def _setup_styles(self):
        # Paleta Glassmorphism React Dark UI
        self.theme = {
            "bg_app": "#060913",
            "bg_nav": "#080d1a",
            "bg_glass": "#0d1428",
            "bg_card": "#131d38",
            "bg_card_alt": "#162242",
            "bg_input": "#080d1a",
            "border_glass": "#233559",
            "border_subtle": "#1b2845",
            "text_primary": "#f8fafc",
            "text_secondary": "#94a3b8",
            "text_muted": "#64748b",
            "cyan": "#38bdf8",
            "violet": "#8b5cf6",
            "emerald": "#10b981",
            "rose": "#f43f5e",
            "amber": "#f59e0b",
        }
        self.root.configure(bg=self.theme["bg_app"])

        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TFrame", background=self.theme["bg_app"])
        style.configure(
            "TCombobox",
            fieldbackground=self.theme["bg_input"],
            background="#1e293b",
            foreground="#f8fafc",
            arrowcolor="#38bdf8",
            bordercolor=self.theme["border_glass"],
            lightcolor=self.theme["border_glass"],
            darkcolor=self.theme["border_glass"],
            padding=4,
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", self.theme["bg_input"])],
            selectbackground=[("readonly", "#1e293b")],
            selectforeground=[("readonly", "#38bdf8")],
        )

    def _create_glass_card(self, parent, title: str, badge: str = "", accent: str = "#38bdf8", pady_outer: int = 5):
        """Crea una tarjeta estilo React Glassmorphism con borde translúcido iluminado y barra de acento."""
        outer = tk.Frame(
            parent,
            bg=self.theme["bg_card"],
            highlightbackground=self.theme["border_glass"],
            highlightcolor=accent,
            highlightthickness=1,
            bd=0,
        )
        outer.pack(fill=tk.X, pady=(0, pady_outer))

        # Barra superior de brillo neón sutil (1px)
        glow_bar = tk.Frame(outer, bg=accent, height=2)
        glow_bar.pack(fill=tk.X, side=tk.TOP)

        hdr = tk.Frame(outer, bg=self.theme["bg_card"], padx=10, pady=5)
        hdr.pack(fill=tk.X)

        tk.Label(
            hdr,
            text=title,
            bg=self.theme["bg_card"],
            fg=self.theme["text_primary"],
            font=("Segoe UI", 9, "bold"),
        ).pack(side=tk.LEFT)

        if badge:
            tk.Label(
                hdr,
                text=badge,
                bg=self.theme["bg_input"],
                fg=accent,
                font=("Segoe UI", 7, "bold"),
                padx=6,
                pady=1,
                highlightbackground=accent,
                highlightthickness=1,
            ).pack(side=tk.RIGHT)

        divider = tk.Frame(outer, bg=self.theme["border_subtle"], height=1)
        divider.pack(fill=tk.X, padx=8)

        body = tk.Frame(outer, bg=self.theme["bg_card"], padx=10, pady=7)
        body.pack(fill=tk.X)

        return body

    def _create_react_button(
        self,
        parent,
        text: str,
        command,
        variant: str = "cyan",
        pady: int = 5,
        padx: int = 8,
        font_size: int = 8,
    ) -> tk.Button:
        """Crea un botón moderno estilo React/Tailwind con efecto hover suave y borde luminoso."""
        palettes = {
            "cyan": ("#0369a1", "#0284c7", "#38bdf8", "#ffffff"),
            "violet": ("#5b21b6", "#6d28d9", "#a78bfa", "#ffffff"),
            "emerald": ("#047857", "#059669", "#34d399", "#ffffff"),
            "rose": ("#be123c", "#e11d48", "#fb7185", "#ffffff"),
            "amber": ("#b45309", "#d97706", "#fbbf24", "#ffffff"),
            "teal": ("#0f766e", "#0d9488", "#2dd4bf", "#ffffff"),
            "glass": ("#17223b", "#223254", "#3b5284", "#e2e8f0"),
        }
        bg_norm, bg_hover, border_col, fg_col = palettes.get(variant, palettes["glass"])

        btn = tk.Button(
            parent,
            text=text,
            command=command,
            bg=bg_norm,
            fg=fg_col,
            activebackground=bg_hover,
            activeforeground="#ffffff",
            relief=tk.FLAT,
            bd=0,
            highlightbackground=border_col,
            highlightcolor=border_col,
            highlightthickness=1,
            font=("Segoe UI", font_size, "bold"),
            padx=padx,
            pady=pady,
            cursor="hand2",
        )

        def _on_enter(e):
            if str(btn["state"]) != "disabled":
                btn.configure(bg=bg_hover)

        def _on_leave(e):
            if str(btn["state"]) != "disabled":
                btn.configure(bg=bg_norm)

        btn.bind("<Enter>", _on_enter)
        btn.bind("<Leave>", _on_leave)
        return btn

    def _build_ui(self):
        # =========================================================================
        # NAVBAR SUPERIOR REACT GLASSMORPHISM
        # =========================================================================
        header = tk.Frame(
            self.root,
            bg=self.theme["bg_nav"],
            height=52,
            padx=16,
            pady=8,
            highlightbackground=self.theme["border_glass"],
            highlightthickness=1,
        )
        header.pack(fill=tk.X, side=tk.TOP)

        brand_frame = tk.Frame(header, bg=self.theme["bg_nav"])
        brand_frame.pack(side=tk.LEFT)

        tk.Label(
            brand_frame,
            text="✦ DEACAM PORTAL",
            bg=self.theme["bg_nav"],
            fg="#38bdf8",
            font=("Segoe UI", 12, "bold"),
        ).pack(side=tk.LEFT, padx=(0, 8))

        tk.Label(
            brand_frame,
            text="REACT GLASS OS • LLAMADA PERPETUA 24/7 • IA BIOMÉTRICA Y MODERACIÓN",
            bg="#111a30",
            fg="#94a3b8",
            font=("Segoe UI", 8, "bold"),
            padx=8,
            pady=3,
            highlightbackground="#233559",
            highlightthickness=1,
        ).pack(side=tk.LEFT)

        self.net_badge = tk.Label(
            header,
            textvariable=self.net_status_var,
            bg="#0c1f24",
            fg="#34d399",
            font=("Segoe UI", 8, "bold"),
            padx=10,
            pady=4,
            highlightbackground="#10b981",
            highlightthickness=1,
        )
        self.net_badge.pack(side=tk.RIGHT)

        self.p2p_badge = tk.Label(
            header,
            textvariable=self.p2p_status_var,
            bg="#151336",
            fg="#c4b5fd",
            font=("Segoe UI", 8, "bold"),
            padx=10,
            pady=4,
            highlightbackground="#8b5cf6",
            highlightthickness=1,
        )
        self.p2p_badge.pack(side=tk.RIGHT, padx=(0, 8))

        self.btn_save_prefs_top = self._create_react_button(
            header,
            "💾 Guardar Preferencias",
            self.save_preferences_manual,
            variant="emerald",
            pady=3,
            padx=10,
            font_size=8,
        )
        self.btn_save_prefs_top.pack(side=tk.RIGHT, padx=(0, 8))

        main_body = tk.Frame(self.root, bg=self.theme["bg_app"])
        main_body.pack(fill=tk.BOTH, expand=True, padx=10, pady=8)

        # =========================================================================
        # COLUMNA 1 (IZQUIERDA): SIDEBAR GLASSMORPHISM CON SCROLL FLUIDO
        # =========================================================================
        sidebar_shell = tk.Frame(
            main_body,
            bg=self.theme["bg_glass"],
            width=400,
            highlightbackground=self.theme["border_glass"],
            highlightthickness=1,
        )
        sidebar_shell.pack(fill=tk.Y, side=tk.LEFT, padx=(0, 10))
        sidebar_shell.pack_propagate(False)

        sb_canvas = tk.Canvas(
            sidebar_shell,
            bg=self.theme["bg_glass"],
            bd=0,
            highlightthickness=0,
            width=382,
        )
        sb_scroll = tk.Scrollbar(
            sidebar_shell,
            orient=tk.VERTICAL,
            command=sb_canvas.yview,
            bg=self.theme["bg_glass"],
            troughcolor=self.theme["bg_app"],
            width=10,
        )
        sidebar = tk.Frame(sb_canvas, bg=self.theme["bg_glass"], padx=10, pady=10)

        sidebar.bind(
            "<Configure>",
            lambda e: sb_canvas.configure(scrollregion=sb_canvas.bbox("all")),
        )
        sb_win_id = sb_canvas.create_window((0, 0), window=sidebar, anchor="nw", width=382)
        sb_canvas.bind(
            "<Configure>",
            lambda e: sb_canvas.itemconfig(sb_win_id, width=e.width),
        )
        sb_canvas.configure(yscrollcommand=sb_scroll.set)

        sb_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        def _on_mousewheel(event):
            try:
                if event.num == 4 or event.delta > 0:
                    sb_canvas.yview_scroll(-1, "units")
                elif event.num == 5 or event.delta < 0:
                    sb_canvas.yview_scroll(1, "units")
            except Exception:
                pass

        sb_canvas.bind("<Enter>", lambda _: sb_canvas.bind_all("<MouseWheel>", _on_mousewheel))
        sb_canvas.bind("<Leave>", lambda _: sb_canvas.unbind_all("<MouseWheel>"))

        # --- CARD 1: PORTAL DE LLAMADA PERPETUA 24/7 (RED P2P + PARLANTE + CÁMARA UNIVERSAL) ---
        p2p_card = self._create_glass_card(
            sidebar,
            "🌐 Portal Llamada Perpetua 24/7",
            badge="P2P LAN • AUTO-JOIN",
            accent=self.theme["cyan"],
        )

        self._add_checkbox(
            p2p_card,
            "🔗 Auto-Unir a Llamada 24/7 al iniciar (Red P2P Recordada)",
            self.auto_join_call_var,
            fg="#34d399",
            bg=self.theme["bg_card"],
            bold=True,
        )

        p2p_btns_row = tk.Frame(p2p_card, bg=self.theme["bg_card"])
        p2p_btns_row.pack(fill=tk.X, pady=(4, 3))

        self._create_react_button(
            p2p_btns_row,
            "🪞 Portal 24/7 Fullscreen",
            lambda: self.open_clean_portal_call_window(borderless_fullscreen=True),
            variant="cyan",
            pady=5,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 3))

        self._create_react_button(
            p2p_btns_row,
            "🔊 Config P2P / Parlante",
            self.open_p2p_and_devices_config_window,
            variant="violet",
            pady=5,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(3, 0))

        self._create_react_button(
            p2p_card,
            "🖥️ Modo Kiosco Independiente (Ocultar Admin — F12 / Clic Der para volver)",
            self.enter_kiosk_portal_only_mode,
            variant="teal",
            pady=4,
            font_size=7,
        ).pack(fill=tk.X, pady=(2, 4))

        src_row = tk.Frame(p2p_card, bg=self.theme["bg_card"])
        src_row.pack(fill=tk.X, pady=(2, 0))
        tk.Label(
            src_row,
            text="Entrada de Cámara:",
            bg=self.theme["bg_card"],
            fg=self.theme["text_secondary"],
            font=("Segoe UI", 8, "bold"),
        ).pack(anchor="w", pady=(0, 2))
        ttk.Combobox(
            src_row,
            textvariable=self.camera_source_mode_var,
            values=[
                "Dahua Dual / Simple PoE (RTSP Automático)",
                "Cámara USB / Webcam Local (Universal)",
                "Cámara IP Universal (URL RTSP / HTTP / ONVIF)",
            ],
            state="readonly",
        ).pack(fill=tk.X)

        # --- CARD 2: GESTOR DE CÁMARAS Y SEÑAL ZERO-LAG ---
        cam_card = self._create_glass_card(
            sidebar,
            "📷 Gestor Dual de Cámaras",
            badge="ZERO-LAG RTSP/USB",
            accent=self.theme["emerald"],
        )

        net_btns = tk.Frame(cam_card, bg=self.theme["bg_card"])
        net_btns.pack(fill=tk.X, pady=(0, 4))

        self._create_react_button(
            net_btns,
            "🔍 Detectar Cámaras",
            self.scan_both_cameras,
            variant="cyan",
            pady=4,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 3))

        self._create_react_button(
            net_btns,
            "🔄 Invertir Cam 1 ↔ Cam 2",
            self.swap_cameras,
            variant="glass",
            pady=4,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(3, 0))

        self._create_react_button(
            cam_card,
            "🛠️ Reparar ARP / Inicializar 2da Cámara en Navegador",
            self.open_dual_ip_wizard,
            variant="amber",
            pady=4,
        ).pack(fill=tk.X, pady=(0, 6))

        # Sub-tarjeta Cámara 1
        c1_sub = tk.Frame(
            cam_card,
            bg=self.theme["bg_glass"],
            padx=8,
            pady=6,
            highlightbackground=self.theme["border_subtle"],
            highlightthickness=1,
        )
        c1_sub.pack(fill=tk.X, pady=(0, 5))
        tk.Label(
            c1_sub,
            text="🌐 CÁMARA 1 • PANORÁMICA (CAMPO ABIERTO)",
            bg=self.theme["bg_glass"],
            fg="#34d399",
            font=("Segoe UI", 8, "bold"),
        ).grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 3))
        self._add_compact_row(c1_sub, "IP Cam 1:", self.ip1_var, "Usuario:", self.user1_var, 1)
        self._add_compact_row(c1_sub, "Clave 1:", self.pass1_var, "MAC 1:", self.mac1_var, 2, show1="*")

        # Sub-tarjeta Cámara 2
        c2_sub = tk.Frame(
            cam_card,
            bg=self.theme["bg_glass"],
            padx=8,
            pady=6,
            highlightbackground=self.theme["border_subtle"],
            highlightthickness=1,
        )
        c2_sub.pack(fill=tk.X, pady=(0, 5))
        tk.Label(
            c2_sub,
            text="👤 CÁMARA 2 • SECUNDARIA (VISTA 1 A 1)",
            bg=self.theme["bg_glass"],
            fg="#fbbf24",
            font=("Segoe UI", 8, "bold"),
        ).grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 2))
        tk.Checkbutton(
            c2_sub,
            text="Integrar 2da Cámara en la misma señal en vivo",
            variable=self.enable_cam2_var,
            bg=self.theme["bg_glass"],
            fg="#e2e8f0",
            selectcolor=self.theme["bg_input"],
            activebackground=self.theme["bg_glass"],
            font=("Segoe UI", 8),
        ).grid(row=1, column=0, columnspan=4, sticky="w", pady=(0, 2))
        self._add_compact_row(c2_sub, "IP Cam 2:", self.ip2_var, "Usuario:", self.user2_var, 2)
        self._add_compact_row(c2_sub, "Clave 2:", self.pass2_var, "MAC 2:", self.mac2_var, 3, show1="*")

        stream_row = tk.Frame(cam_card, bg=self.theme["bg_card"])
        stream_row.pack(fill=tk.X, pady=(2, 5))
        tk.Label(stream_row, text="Perfil RTSP:", bg=self.theme["bg_card"], fg=self.theme["text_secondary"], font=("Segoe UI", 8)).pack(side=tk.LEFT)
        ttk.Combobox(
            stream_row,
            textvariable=self.stream_quality_var,
            values=[
                "0 (Principal HD - Con Drenado Zero-Lag)",
                "1 (Subflujo Ultra-Rápido - Baja Latencia)",
            ],
            state="readonly",
            width=33,
        ).pack(side=tk.RIGHT)

        conn_frame = tk.Frame(cam_card, bg=self.theme["bg_card"])
        conn_frame.pack(fill=tk.X, pady=(2, 0))

        self.btn_connect = self._create_react_button(
            conn_frame,
            "▶ INICIAR SEÑAL TURBO",
            self.start_unified_signal,
            variant="emerald",
            pady=6,
            font_size=9,
        )
        self.btn_connect.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 3))

        self.btn_disconnect = self._create_react_button(
            conn_frame,
            "⏹ Detener",
            self.stop_unified_signal,
            variant="rose",
            pady=6,
            font_size=9,
        )
        self.btn_disconnect.configure(state=tk.DISABLED)
        self.btn_disconnect.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(3, 0))

        # --- CARD 3: IA, MODERACIÓN DE SEÑAS/CELULARES Y MOVIMIENTOS FLUIDOS ---
        ai_card = self._create_glass_card(
            sidebar,
            "🛡️ Moderación IA y Cabezal Fluido",
            badge="2-STAGE AI VERIFY",
            accent=self.theme["rose"],
        )

        tk.Label(
            ai_card,
            text="Composición de Escena:",
            bg=self.theme["bg_card"],
            fg=self.theme["text_secondary"],
            font=("Segoe UI", 8, "bold"),
        ).pack(anchor="w", pady=(0, 2))

        mode_cb = ttk.Combobox(
            ai_card,
            textvariable=self.signal_mode_var,
            values=[
                "Auto-Director IA (Conmuta Panorámica <-> 1 a 1)",
                "Portal Dual (1 a 1 Principal + Panorámica PiP)",
                "Pantalla Dividida (Campo Abierto + 1 a 1 Lado a Lado)",
                "Solo Cámara 1 (Panorámica con Auto-Zoom IA)",
                "Solo Cámara 2 (Vista 1 a 1 Directa)",
            ],
            state="readonly",
        )
        mode_cb.pack(fill=tk.X, pady=(0, 6))

        self._create_react_button(
            ai_card,
            "🛡️ Configurar Catálogo de Señas, Celulares y Evidencias",
            self.open_moderation_catalog_window,
            variant="rose",
            pady=5,
        ).pack(fill=tk.X, pady=(0, 6))

        toggles_box = tk.Frame(
            ai_card,
            bg=self.theme["bg_glass"],
            padx=8,
            pady=6,
            highlightbackground=self.theme["border_subtle"],
            highlightthickness=1,
        )
        toggles_box.pack(fill=tk.X, pady=(0, 6))

        self._add_checkbox(
            toggles_box,
            "👁️‍🗨️ Ocultar Vista Panorámica si el foco está en 1 a 1 y viceversa (Sin PiP)",
            self.hide_inactive_cam_pip_var,
            fg="#34d399",
            bg=self.theme["bg_glass"],
            bold=True,
        )
        self._add_checkbox(toggles_box, "🪞 Imagen en Modo Espejo (Limpia sin Textos OSD)", self.mirror_mode_var, fg="#38bdf8", bg=self.theme["bg_glass"])
        self._add_checkbox(toggles_box, "🚫 Activar Blureo Automático de Señas y Actos Indebidos", self.offensive_blur_var, fg="#fda4af", bg=self.theme["bg_glass"])
        self._add_checkbox(toggles_box, "📸📱 Detectar Uso de Celular / Toma de Fotografías o Video", self.rule_cellphone_use_var, fg="#fdba74", bg=self.theme["bg_glass"])
        self._add_checkbox(toggles_box, "👤 Blurear Persona Completa al Detectar Acto Indebido", self.blur_full_person_var, fg="#fda4af", bg=self.theme["bg_glass"])
        self._add_checkbox(toggles_box, "🔄 Oscilar Cámara (Izq ↔ Der) Ultra Suave sin movimiento", self.idle_patrol_var, fg="#fde047", bg=self.theme["bg_glass"])
        self._add_checkbox(toggles_box, "👁️ Encuadre y Zoom IA en Contacto Visual / Saludo", self.auto_frame_var, bg=self.theme["bg_glass"])
        self._add_checkbox(toggles_box, "🛡️ Difuminar Lente Completo si alguien lo obstruye", self.obstruction_blur_var, bg=self.theme["bg_glass"])
        self._add_checkbox(toggles_box, "🌀 Máscara Circular 'El Portal' (Estilo NY - Dublín)", self.circular_portal_var, bg=self.theme["bg_glass"])
        self._add_checkbox(toggles_box, "✨ Mostrar Recuadros de IA en Video (Opcional)", self.show_overlays_var, bg=self.theme["bg_glass"])

        # Sliders con estética Glassmorphism
        sens_frame = tk.Frame(ai_card, bg=self.theme["bg_card"])
        sens_frame.pack(fill=tk.X, pady=(2, 0))
        tk.Label(sens_frame, text="Sensibilidad Antiseñas Ofensivas:", bg=self.theme["bg_card"], fg="#fda4af", font=("Segoe UI", 8, "bold")).pack(side=tk.LEFT)
        self.sens_lbl = tk.Label(sens_frame, text="Alta (75%)", bg=self.theme["bg_card"], fg="#fb7185", font=("Segoe UI", 8, "bold"))
        self.sens_lbl.pack(side=tk.RIGHT)

        tk.Scale(
            ai_card,
            from_=0.40,
            to=1.00,
            resolution=0.05,
            orient=tk.HORIZONTAL,
            variable=self.mod_sensitivity_var,
            bg=self.theme["bg_card"],
            fg="#e2e8f0",
            activebackground="#f43f5e",
            bd=0,
            sliderrelief=tk.FLAT,
            highlightthickness=0,
            troughcolor=self.theme["bg_input"],
            showvalue=False,
            command=lambda v: self.sens_lbl.configure(text=f"{'Ultra Estricta' if float(v)>=0.85 else 'Alta' if float(v)>=0.70 else 'Media'} ({int(float(v)*100)}%)"),
        ).pack(fill=tk.X, pady=(0, 3))

        s_frame = tk.Frame(ai_card, bg=self.theme["bg_card"])
        s_frame.pack(fill=tk.X, pady=(1, 0))
        tk.Label(s_frame, text="Suavidad de Movimiento / Oscilación:", bg=self.theme["bg_card"], fg="#cbd5e1", font=("Segoe UI", 8)).pack(side=tk.LEFT)
        self.smooth_lbl = tk.Label(s_frame, text="Ultra Suave y Lento (0.45)", bg=self.theme["bg_card"], fg="#34d399", font=("Segoe UI", 8, "bold"))
        self.smooth_lbl.pack(side=tk.RIGHT)

        tk.Scale(
            ai_card,
            from_=0.15,
            to=2.20,
            resolution=0.05,
            orient=tk.HORIZONTAL,
            variable=self.smooth_omega_var,
            bg=self.theme["bg_card"],
            fg="#e2e8f0",
            activebackground="#10b981",
            bd=0,
            sliderrelief=tk.FLAT,
            highlightthickness=0,
            troughcolor=self.theme["bg_input"],
            showvalue=False,
            command=self._update_smooth_label,
        ).pack(fill=tk.X, pady=(0, 3))

        z_frame = tk.Frame(ai_card, bg=self.theme["bg_card"])
        z_frame.pack(fill=tk.X, pady=(1, 0))
        tk.Label(z_frame, text="Zoom Máximo en Interacción 1 a 1:", bg=self.theme["bg_card"], fg="#cbd5e1", font=("Segoe UI", 8)).pack(side=tk.LEFT)
        self.zoom_lbl = tk.Label(z_frame, text=f"{self.max_zoom_var.get():.2f}x", bg=self.theme["bg_card"], fg="#38bdf8", font=("Segoe UI", 8, "bold"))
        self.zoom_lbl.pack(side=tk.RIGHT)

        tk.Scale(
            ai_card,
            from_=1.15,
            to=2.80,
            resolution=0.05,
            orient=tk.HORIZONTAL,
            variable=self.max_zoom_var,
            bg=self.theme["bg_card"],
            fg="#e2e8f0",
            activebackground="#38bdf8",
            bd=0,
            sliderrelief=tk.FLAT,
            highlightthickness=0,
            troughcolor=self.theme["bg_input"],
            showvalue=False,
            command=lambda v: self.zoom_lbl.configure(text=f"{float(v):.2f}x"),
        ).pack(fill=tk.X, pady=(0, 6))
        self._update_smooth_label(self.smooth_omega_var.get())

        action_row = tk.Frame(ai_card, bg=self.theme["bg_card"])
        action_row.pack(fill=tk.X, pady=2)
        self._create_react_button(
            action_row,
            "🌐 Lente Completo",
            self.reset_full_lens,
            variant="glass",
            pady=4,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 3))
        self._create_react_button(
            action_row,
            "🎯 AutoFocus Óptico",
            self.trigger_both_autofocus,
            variant="teal",
            pady=4,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(3, 0))

        media_row = tk.Frame(ai_card, bg=self.theme["bg_card"])
        media_row.pack(fill=tk.X, pady=2)
        self._create_react_button(
            media_row,
            "📸 Foto",
            self.take_snapshot,
            variant="cyan",
            pady=4,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 3))
        self.btn_record = self._create_react_button(
            media_row,
            "⏺ Grabar",
            self.toggle_recording,
            variant="glass",
            pady=4,
        )
        self.btn_record.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(3, 3))
        self._create_react_button(
            media_row,
            "📂 Capturas",
            lambda: os.startfile(self.captures_dir),
            variant="glass",
            pady=4,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(3, 0))

        ev_row = tk.Frame(ai_card, bg=self.theme["bg_card"])
        ev_row.pack(fill=tk.X, pady=(3, 0))
        self._create_react_button(
            ev_row,
            "⚡ Super Screenshot Evidencia",
            self.trigger_manual_evidence_screenshot,
            variant="rose",
            pady=4,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 3))
        self._create_react_button(
            ev_row,
            "🚨 Evidencias Mal Uso",
            lambda: os.startfile(self.evidences_dir),
            variant="amber",
            pady=4,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(3, 0))

        # --- CARD 4: JUEGOS INTERACTIVOS TIPO KINECT AR (GESTOS DE EXTREMIDADES Y PELLIZCO) ---
        kinect_card = self._create_glass_card(
            sidebar,
            "🎮 Juegos Tipo Kinect AR",
            badge="PINCH & LIMB TRACKING",
            accent=self.theme["violet"],
        )

        self._add_checkbox(
            kinect_card,
            "🎮 Mostrar Botón Flotante de Juegos (Gesto de Pellizco 🤏 para entrar)",
            self.enable_kinect_games_hud_var,
            fg="#c4b5fd",
            bg=self.theme["bg_card"],
            bold=True,
        )

        tk.Label(
            kinect_card,
            text="Modo de Jugadores (Solitario / Local 2+ / Llamada P2P):",
            bg=self.theme["bg_card"],
            fg=self.theme["text_secondary"],
            font=("Segoe UI", 8, "bold"),
        ).pack(anchor="w", pady=(3, 2))

        ttk.Combobox(
            kinect_card,
            textvariable=self.kinect_play_mode_var,
            values=[
                "Automático (Solitario / Multijugador / Llamada P2P)",
                "Solitario (1 Jugador vs Guardián IA)",
                "Multijugador Local (2 o más Jugadores en Escena)",
                "Llamada P2P 24/7 (Portal vs Portal en Red)",
            ],
            state="readonly",
        ).pack(fill=tk.X, pady=(0, 5))

        k_row1 = tk.Frame(kinect_card, bg=self.theme["bg_card"])
        k_row1.pack(fill=tk.X, pady=2)
        self._create_react_button(
            k_row1,
            "🕹️ Abrir Menú Flotante Kinect",
            self.kinect_engine.open_menu,
            variant="violet",
            pady=4,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 3))
        self._create_react_button(
            k_row1,
            "✕ Salir Juego",
            self.kinect_engine.close_games,
            variant="glass",
            pady=4,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(3, 0))

        k_row2 = tk.Frame(kinect_card, bg=self.theme["bg_card"])
        k_row2.pack(fill=tk.X, pady=2)
        self._create_react_button(
            k_row2,
            "🏓 Air Pong",
            lambda: self.kinect_engine.start_game("pong"),
            variant="cyan",
            pady=4,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 2))
        self._create_react_button(
            k_row2,
            "🍉 Slicer",
            lambda: self.kinect_engine.start_game("slicer"),
            variant="rose",
            pady=4,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
        self._create_react_button(
            k_row2,
            "🫧 Burbujas",
            lambda: self.kinect_engine.start_game("bubbles"),
            variant="emerald",
            pady=4,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
        self._create_react_button(
            k_row2,
            "🎨 Pintar",
            lambda: self.kinect_engine.start_game("paint"),
            variant="amber",
            pady=4,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(2, 0))

        # --- CARD 5: IA INTERPRETATIVA PUNTUAL CON GEMINI API (MODO AHORRO DE TOKENS) ---
        gem_card = self._create_glass_card(
            sidebar,
            "✨ Copiloto Gemini API (Puntual)",
            badge="LOW-TOKEN SAVER",
            accent=self.theme["amber"],
        )

        self._add_checkbox(
            gem_card,
            "✨ Activar Gemini SOLO en Casos Puntuales (Sin gasto continuo)",
            self.gemini_enabled_var,
            fg="#fde047",
            bg=self.theme["bg_card"],
            bold=True,
        )

        key_row = tk.Frame(gem_card, bg=self.theme["bg_card"])
        key_row.pack(fill=tk.X, pady=(3, 2))
        tk.Label(key_row, text="API Key Gemini:", bg=self.theme["bg_card"], fg=self.theme["text_secondary"], font=("Segoe UI", 8)).pack(side=tk.LEFT)
        tk.Entry(
            key_row,
            textvariable=self.gemini_api_key_var,
            show="*",
            bg=self.theme["bg_input"],
            fg="#f8fafc",
            insertbackground="#38bdf8",
            relief=tk.FLAT,
            highlightbackground=self.theme["border_glass"],
            highlightcolor="#f59e0b",
            highlightthickness=1,
            font=("Segoe UI", 8),
        ).pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(6, 0))

        mod_row = tk.Frame(gem_card, bg=self.theme["bg_card"])
        mod_row.pack(fill=tk.X, pady=2)
        tk.Label(mod_row, text="Modelo:", bg=self.theme["bg_card"], fg=self.theme["text_secondary"], font=("Segoe UI", 8)).pack(side=tk.LEFT)
        ttk.Combobox(
            mod_row,
            textvariable=self.gemini_model_var,
            values=[
                "gemini-2.5-flash (Recomendado - Ultra Económico)",
                "gemini-2.5-flash-lite (Mínimo Costo de Tokens)",
                "gemini-2.5-pro (Razonamiento Profundo)",
            ],
            state="readonly",
            width=36,
        ).pack(side=tk.RIGHT)

        self._add_checkbox(
            gem_card,
            "🛡️ Auditar solo Actos Ofensivos / Dudosos (Evita Falsos Positivos)",
            self.gemini_trigger_offense_var,
            fg="#e2e8f0",
            bg=self.theme["bg_card"],
        )
        self._add_checkbox(
            gem_card,
            "👤 Enriquecer Perfil 1 sola vez al Iniciar Conexión 1 a 1",
            self.gemini_trigger_1to1_var,
            fg="#e2e8f0",
            bg=self.theme["bg_card"],
        )

        lim_row = tk.Frame(gem_card, bg=self.theme["bg_card"])
        lim_row.pack(fill=tk.X, pady=(3, 2))
        tk.Label(lim_row, text="Enfriamiento (s):", bg=self.theme["bg_card"], fg=self.theme["text_secondary"], font=("Segoe UI", 8)).pack(side=tk.LEFT)
        tk.Spinbox(
            lim_row,
            from_=10,
            to=300,
            increment=5,
            textvariable=self.gemini_cooldown_var,
            width=5,
            bg=self.theme["bg_input"],
            fg="#f8fafc",
            buttonbackground="#1e293b",
            relief=tk.FLAT,
            font=("Segoe UI", 8),
        ).pack(side=tk.LEFT, padx=(4, 10))
        tk.Label(lim_row, text="Máx/Hora:", bg=self.theme["bg_card"], fg=self.theme["text_secondary"], font=("Segoe UI", 8)).pack(side=tk.LEFT)
        tk.Spinbox(
            lim_row,
            from_=5,
            to=200,
            increment=5,
            textvariable=self.gemini_max_calls_hr_var,
            width=5,
            bg=self.theme["bg_input"],
            fg="#f8fafc",
            buttonbackground="#1e293b",
            relief=tk.FLAT,
            font=("Segoe UI", 8),
        ).pack(side=tk.LEFT, padx=(4, 0))

        tk.Label(
            gem_card,
            textvariable=self.gemini_usage_var,
            bg=self.theme["bg_input"],
            fg="#fbbf24",
            font=("Segoe UI", 7, "bold"),
            padx=6,
            pady=3,
            highlightbackground=self.theme["border_subtle"],
            highlightthickness=1,
        ).pack(fill=tk.X, pady=(3, 4))

        self._create_react_button(
            gem_card,
            "⚡ Consulta Puntual Gemini Ahora (1 Recorte Compacto 384px)",
            self.trigger_manual_gemini_analysis,
            variant="amber",
            pady=5,
        ).pack(fill=tk.X)

        # --- CARD 6: CONTROL DE PREFERENCIAS DEL PANEL DE ADMINISTRADOR ---
        prefs_card = self._create_glass_card(
            sidebar,
            "💾 Guardar Preferencias del Panel",
            badge="CONFIG_PORTAL.JSON",
            accent=self.theme["emerald"],
        )

        self._add_checkbox(
            prefs_card,
            "⚡ Auto-Guardar cambios del Panel en tiempo real",
            self.auto_save_prefs_var,
            fg="#34d399",
            bg=self.theme["bg_card"],
            bold=True,
        )

        self.btn_save_prefs_card = self._create_react_button(
            prefs_card,
            "💾 Guardar Preferencias del Panel Ahora",
            self.save_preferences_manual,
            variant="emerald",
            pady=6,
            font_size=9,
        )
        self.btn_save_prefs_card.pack(fill=tk.X, pady=(4, 3))

        p_row = tk.Frame(prefs_card, bg=self.theme["bg_card"])
        p_row.pack(fill=tk.X, pady=2)
        self._create_react_button(
            p_row,
            "📂 Recargar Guardadas",
            self.reload_saved_preferences,
            variant="glass",
            pady=4,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 3))
        self._create_react_button(
            p_row,
            "🔄 Restaurar por Defecto",
            self.reset_preferences_to_default,
            variant="amber",
            pady=4,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(3, 0))

        # =========================================================================
        # COLUMNA 3 (DERECHA): INSPECTOR REACT GLASS DE DETALLE BIOMÉTRICO IA
        # =========================================================================
        profile_panel = tk.Frame(
            main_body,
            bg=self.theme["bg_glass"],
            width=355,
            padx=12,
            pady=10,
            highlightbackground=self.theme["border_glass"],
            highlightthickness=1,
        )
        profile_panel.pack(fill=tk.Y, side=tk.RIGHT, padx=(10, 0))
        profile_panel.pack_propagate(False)

        prof_glow = tk.Frame(profile_panel, bg=self.theme["violet"], height=2)
        prof_glow.pack(fill=tk.X, side=tk.TOP, pady=(0, 8))

        prof_hdr = tk.Frame(profile_panel, bg=self.theme["bg_glass"])
        prof_hdr.pack(fill=tk.X, pady=(0, 6))

        tk.Label(
            prof_hdr,
            text="🧬 Inspector Biométrico IA",
            bg=self.theme["bg_glass"],
            fg="#c4b5fd",
            font=("Segoe UI", 10, "bold"),
        ).pack(side=tk.LEFT)

        self._create_react_button(
            prof_hdr,
            "🗗 Ventana Flotante",
            self.open_floating_profile_window,
            variant="violet",
            pady=3,
            padx=8,
            font_size=8,
        ).pack(side=tk.RIGHT)

        tk.Label(
            profile_panel,
            text="Telemetría humana en tiempo real: Género, Edad, Perfil Visual, Apariencia Física, Vestimenta y Moderación.",
            bg=self.theme["bg_glass"],
            fg=self.theme["text_secondary"],
            font=("Segoe UI", 8),
            wraplength=325,
            justify=tk.LEFT,
        ).pack(anchor="w", pady=(0, 8))

        self.profile_text = tk.Text(
            profile_panel,
            bg=self.theme["bg_input"],
            fg="#e2e8f0",
            font=("Segoe UI", 9),
            relief=tk.FLAT,
            padx=12,
            pady=12,
            wrap=tk.WORD,
            highlightbackground=self.theme["border_subtle"],
            highlightthickness=1,
            state=tk.DISABLED,
        )
        self.profile_text.pack(fill=tk.BOTH, expand=True)

        self.profile_text.tag_configure("header", foreground="#38bdf8", font=("Segoe UI", 10, "bold"))
        self.profile_text.tag_configure("person_title", foreground="#34d399", font=("Segoe UI", 10, "bold"))
        self.profile_text.tag_configure("offensive_title", foreground="#fb7185", font=("Segoe UI", 10, "bold"))
        self.profile_text.tag_configure("label", foreground="#94a3b8", font=("Segoe UI", 9, "bold"))
        self.profile_text.tag_configure("value", foreground="#f8fafc", font=("Segoe UI", 9))
        self.profile_text.tag_configure("econ", foreground="#fbbf24", font=("Segoe UI", 9, "bold"))
        self.profile_text.tag_configure("alert", foreground="#f43f5e", font=("Segoe UI", 9, "bold"))

        # =========================================================================
        # COLUMNA 2 (CENTRO): MONITOR DE SEÑAL UNIFICADA GLASSMORPHISM
        # =========================================================================
        center_panel = tk.Frame(
            main_body,
            bg="#05070f",
            highlightbackground=self.theme["border_glass"],
            highlightthickness=1,
        )
        center_panel.pack(fill=tk.BOTH, expand=True, side=tk.LEFT)

        ai_bar = tk.Frame(
            center_panel,
            bg=self.theme["bg_card"],
            height=40,
            padx=14,
            pady=6,
            highlightbackground=self.theme["border_subtle"],
            highlightthickness=1,
        )
        ai_bar.pack(fill=tk.X, side=tk.TOP)

        self.ai_mode_lbl = tk.Label(
            ai_bar,
            textvariable=self.ai_banner_var,
            bg=self.theme["bg_card"],
            fg="#34d399",
            font=("Segoe UI", 10, "bold"),
        )
        self.ai_mode_lbl.pack(side=tk.LEFT)

        self.ai_stats_lbl = tk.Label(
            ai_bar,
            textvariable=self.ai_telemetry_var,
            bg=self.theme["bg_input"],
            fg="#7dd3fc",
            font=("Segoe UI", 8, "bold"),
            padx=10,
            pady=3,
            highlightbackground=self.theme["border_glass"],
            highlightthickness=1,
        )
        self.ai_stats_lbl.pack(side=tk.RIGHT)

        self.video_label = tk.Label(
            center_panel,
            text="✦ DEACAM PORTAL 24/7 • MOTOR REACT GLASS ZERO-LAG\n\n"
                 "• Movimientos y Zoom Sub-Píxel Ultra Suaves y Lentos por defecto\n"
                 "• Llamada Perpetua 24/7 en Red P2P con Auto-Unión y Reconexión Infinita\n"
                 "• Juegos Interactivos Tipo Kinect AR con Gesto de Pellizco (🤏) y Extremidades\n"
                 "• Moderación IA en 2 Etapas (Pre-Análisis + Post-Análisis) y Evidencias Instantáneas",
            bg="#05070f",
            fg="#94a3b8",
            font=("Segoe UI", 10),
            justify=tk.CENTER,
        )
        self.video_label.pack(fill=tk.BOTH, expand=True)
        self._bind_kinect_mouse_assist(self.video_label)

        statusbar = tk.Frame(
            self.root,
            bg=self.theme["bg_nav"],
            height=28,
            padx=14,
            highlightbackground=self.theme["border_subtle"],
            highlightthickness=1,
        )
        statusbar.pack(fill=tk.X, side=tk.BOTTOM)
        tk.Label(statusbar, textvariable=self.status_var, bg=self.theme["bg_nav"], fg="#94a3b8", font=("Segoe UI", 8)).pack(side=tk.LEFT)
        self.fps_lbl = tk.Label(statusbar, text="⚡ FPS: -- | Señal: 1280x720", bg=self.theme["bg_nav"], fg="#38bdf8", font=("Segoe UI", 8, "bold"))
        self.fps_lbl.pack(side=tk.RIGHT)

    def open_moderation_catalog_window(self):
        """
        Abre el Panel de Control del Catálogo de Señas y Actos Ofensivos con interfaz React Glassmorphism.
        """
        if self.catalog_win is not None and self.catalog_win.winfo_exists():
            self.catalog_win.lift()
            return

        win = tk.Toplevel(self.root)
        win.title("🛡️ Catálogo React Glass — Moderación de Señas, Celulares y Evidencias")
        win.geometry("600x590")
        win.configure(bg=self.theme["bg_app"], padx=18, pady=14)
        win.transient(self.root)

        rules_card = self._create_glass_card(
            win,
            "🛡️ Reglas Activas de Censura y Moderación IA",
            badge="2-STAGE VERIFICATION",
            accent=self.theme["rose"],
            pady_outer=10,
        )

        tk.Label(
            rules_card,
            text="Selecciona los actos confirmados que el Portal blureará automáticamente en tiempo real:",
            bg=self.theme["bg_card"],
            fg=self.theme["text_secondary"],
            font=("Segoe UI", 8),
            wraplength=540,
            justify=tk.LEFT,
        ).pack(anchor="w", pady=(0, 6))

        catalog_items = [
            ("🖕 1. Dedo Medio / 'V' Inversa Confirmada (Esqueleto 3D 21 Puntos)", self.rule_middle_finger_var),
            ("🔫 2. Seña de Pistola / Arma Apuntando Confirmada (Índice + Pulgar)", self.rule_gun_gesture_var),
            ("🤘 3. Seña de Cuernos / Insulto Confirmado (Índice + Meñique)", self.rule_horns_insult_var),
            ("👎 4. Pulgar Abajo Confirmado (Abucheo / Rechazo Hostil)", self.rule_thumbs_down_var),
            ("📸📱 5. Uso Indebido de Teléfono Celular / Tomar Fotografías o Video", self.rule_cellphone_use_var),
            ("✊ 6. Puño Cerrado Amenazante Levantado (Opcional - Desactivado por defecto)", self.rule_fist_threat_var),
        ]

        for text, var in catalog_items:
            self._add_checkbox(rules_card, text, var, fg="#f8fafc", bg=self.theme["bg_card"])

        self._add_checkbox(
            rules_card,
            "📸 Guardar Super Screenshot Forense en 'evidencias_mal_uso' al confirmar acto",
            self.auto_evidence_var,
            fg="#fbbf24",
            bg=self.theme["bg_card"],
            bold=True,
        )

        tune_card = self._create_glass_card(
            win,
            "⚙️ Ajuste Fino de Confirmación Post-Análisis",
            badge="ZERO FALSE POSITIVES",
            accent=self.theme["amber"],
            pady_outer=12,
        )

        tk.Label(
            tune_card,
            text="Rigurosidad Geométrica de Mano 3D:",
            bg=self.theme["bg_card"],
            fg="#e2e8f0",
            font=("Segoe UI", 8, "bold"),
        ).pack(anchor="w")
        tk.Scale(
            tune_card,
            from_=0.40,
            to=1.00,
            resolution=0.05,
            orient=tk.HORIZONTAL,
            variable=self.mod_sensitivity_var,
            bg=self.theme["bg_card"],
            fg="#fb7185",
            activebackground="#f43f5e",
            bd=0,
            sliderrelief=tk.FLAT,
            highlightthickness=0,
            troughcolor=self.theme["bg_input"],
        ).pack(fill=tk.X, pady=(0, 6))

        tk.Label(
            tune_card,
            text="Segundos de Retención del Blur mientras se ejecuta el acto (0.85s = solo durante el acto):",
            bg=self.theme["bg_card"],
            fg="#e2e8f0",
            font=("Segoe UI", 8, "bold"),
        ).pack(anchor="w")
        tk.Scale(
            tune_card,
            from_=0.4,
            to=4.0,
            resolution=0.15,
            orient=tk.HORIZONTAL,
            variable=self.mod_hold_seconds_var,
            bg=self.theme["bg_card"],
            fg="#38bdf8",
            activebackground="#38bdf8",
            bd=0,
            sliderrelief=tk.FLAT,
            highlightthickness=0,
            troughcolor=self.theme["bg_input"],
        ).pack(fill=tk.X)

        btn_row = tk.Frame(win, bg=self.theme["bg_app"])
        btn_row.pack(fill=tk.X)
        self._create_react_button(
            btn_row,
            "✅ Activar Todas las Reglas",
            lambda: [v.set(True) for _, v in catalog_items],
            variant="emerald",
            pady=7,
            font_size=9,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))

        self._create_react_button(
            btn_row,
            "Cerrar Panel",
            win.destroy,
            variant="glass",
            pady=7,
            font_size=9,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 0))

        self.catalog_win = win

    def _add_compact_row(self, parent, lbl1, var1, lbl2, var2, row, show1=None):
        bg_p = parent.cget("bg")
        tk.Label(parent, text=lbl1, bg=bg_p, fg="#94a3b8", font=("Segoe UI", 8)).grid(row=row, column=0, sticky="w", pady=2)
        tk.Entry(
            parent,
            textvariable=var1,
            show=show1,
            bg=self.theme["bg_input"],
            fg="#f8fafc",
            insertbackground="#38bdf8",
            relief=tk.FLAT,
            highlightbackground=self.theme["border_glass"],
            highlightcolor="#38bdf8",
            highlightthickness=1,
            width=13,
            font=("Segoe UI", 8),
        ).grid(row=row, column=1, sticky="ew", padx=(4, 6), pady=2)
        tk.Label(parent, text=lbl2, bg=bg_p, fg="#94a3b8", font=("Segoe UI", 8)).grid(row=row, column=2, sticky="w", pady=2)
        tk.Entry(
            parent,
            textvariable=var2,
            bg=self.theme["bg_input"],
            fg="#cbd5e1",
            insertbackground="#38bdf8",
            relief=tk.FLAT,
            highlightbackground=self.theme["border_glass"],
            highlightcolor="#38bdf8",
            highlightthickness=1,
            width=13,
            font=("Segoe UI", 8),
        ).grid(row=row, column=3, sticky="ew", padx=(4, 0), pady=2)
        parent.columnconfigure(1, weight=1)
        parent.columnconfigure(3, weight=1)

    def _add_checkbox(self, parent, text, var, fg="#e2e8f0", bg=None, bold=False):
        bg_col = bg or parent.cget("bg")
        tk.Checkbutton(
            parent,
            text=text,
            variable=var,
            bg=bg_col,
            fg=fg,
            selectcolor=self.theme["bg_input"],
            activebackground=bg_col,
            activeforeground="#38bdf8",
            font=("Segoe UI", 8, "bold" if bold else "normal"),
            anchor="w",
            cursor="hand2",
        ).pack(fill=tk.X, pady=1)

    def _update_smooth_label(self, val):
        v = float(val)
        if v <= 0.65:
            txt = f"Ultra Suave y Lento ({v:.2f})"
        elif v <= 1.25:
            txt = f"Cinematográfico Suave ({v:.2f})"
        else:
            txt = f"Fluido ({v:.2f})"
        self.smooth_lbl.configure(text=txt)

    def open_floating_profile_window(self):
        if self.separate_profile_win is not None and self.separate_profile_win.winfo_exists():
            self.separate_profile_win.lift()
            return

        win = tk.Toplevel(self.root)
        win.title("🧬 Inspector Flotante React Glass — Detalle General de Personas (IA)")
        win.geometry("500x700")
        win.configure(bg=self.theme["bg_app"], padx=14, pady=12)

        card = self._create_glass_card(
            win,
            "🧬 Inspector Biométrico y Moderación en Tiempo Real",
            badge="LIVE AI TELEMETRY",
            accent=self.theme["violet"],
        )

        txt_widget = tk.Text(
            card,
            bg=self.theme["bg_input"],
            fg="#e2e8f0",
            font=("Segoe UI", 10),
            relief=tk.FLAT,
            padx=12,
            pady=12,
            wrap=tk.WORD,
            highlightbackground=self.theme["border_subtle"],
            highlightthickness=1,
            state=tk.DISABLED,
        )
        txt_widget.pack(fill=tk.BOTH, expand=True)

        txt_widget.tag_configure("header", foreground="#38bdf8", font=("Segoe UI", 11, "bold"))
        txt_widget.tag_configure("person_title", foreground="#34d399", font=("Segoe UI", 11, "bold"))
        txt_widget.tag_configure("offensive_title", foreground="#fb7185", font=("Segoe UI", 11, "bold"))
        txt_widget.tag_configure("label", foreground="#94a3b8", font=("Segoe UI", 9, "bold"))
        txt_widget.tag_configure("value", foreground="#f8fafc", font=("Segoe UI", 9))
        txt_widget.tag_configure("econ", foreground="#fbbf24", font=("Segoe UI", 9, "bold"))
        txt_widget.tag_configure("alert", foreground="#f43f5e", font=("Segoe UI", 10, "bold"))

        self.separate_profile_win = win
        self.separate_profile_txt = txt_widget
        self.last_profile_signature = ""

    def _update_profile_panels(self, profiles_cam1: list, profiles_cam2: list, mode_str: str, motion_pct: float):
        now = time.time()
        if now - self.last_profile_update < 0.65:
            return
        self.last_profile_update = now

        all_profs = []
        for p in profiles_cam1:
            p_copy = dict(p)
            p_copy["cam"] = "Cámara 1 (Panorámica)"
            all_profs.append(p_copy)
        for p in profiles_cam2:
            p_copy = dict(p)
            p_copy["cam"] = "Cámara 2 (Vista 1 a 1)"
            all_profs.append(p_copy)

        gem_info = self.gemini_verifier.last_interpretation
        sig = json.dumps(all_profs, sort_keys=True) + mode_str + str(gem_info.get("timestamp", ""))
        if sig == self.last_profile_signature:
            return
        self.last_profile_signature = sig

        widgets = [self.profile_text]
        if self.separate_profile_win is not None and self.separate_profile_win.winfo_exists() and self.separate_profile_txt is not None:
            widgets.append(self.separate_profile_txt)

        for w in widgets:
            w.configure(state=tk.NORMAL)
            w.delete("1.0", tk.END)

            w.insert(tk.END, "📊 RESUMEN DE ESCENA\n", "header")
            w.insert(tk.END, "Estado: ", "label")
            w.insert(tk.END, f"{mode_str}\n", "value")
            w.insert(tk.END, "Personas detectadas: ", "label")
            w.insert(tk.END, f"{len(all_profs)}   |   ", "value")
            w.insert(tk.END, "Movimiento: ", "label")
            w.insert(tk.END, f"{motion_pct:.1f}%\n", "value")

            # Bloque de IA Interpretativa Puntual Gemini (Bajo Consumo de Tokens)
            w.insert(tk.END, "\n✨ COPILOTO PUNTUAL GEMINI API\n", "econ")
            w.insert(tk.END, "• Consumo: ", "label")
            w.insert(tk.END, f"{self.gemini_verifier.get_usage_summary()}\n", "value")
            w.insert(tk.END, f"• Última Consulta ({gem_info.get('timestamp', '--')}): ", "label")
            w.insert(tk.END, f"{gem_info.get('trigger_reason', '--')}\n", "value")
            w.insert(tk.END, "• Interpretación: ", "label")
            w.insert(
                tk.END,
                f"{gem_info.get('context_summary', '--')}\n",
                "alert" if gem_info.get("is_offensive_confirmed") else "value",
            )
            if gem_info.get("attire_and_attitude", "--") != "--":
                w.insert(tk.END, "• Actitud/Contexto: ", "label")
                w.insert(tk.END, f"{gem_info.get('attire_and_attitude')}\n", "value")

            w.insert(tk.END, "─" * 38 + "\n\n", "label")

            if not all_profs:
                w.insert(
                    tk.END,
                    "Sin personas detectadas en este momento.\n\n"
                    "🔄 La cámara realiza oscilación automática de izquierda a derecha buscando actividad.",
                    "label",
                )
            else:
                for idx, p in enumerate(all_profs, start=1):
                    is_off = "SÍ" in p["offensive"]
                    title_tag = "offensive_title" if is_off else "person_title"
                    w.insert(tk.END, f"👤 PERSONA #{idx} — {p['cam']}\n", title_tag)

                    w.insert(tk.END, "• Sexo / Género: ", "label")
                    w.insert(tk.END, f"{p['gender']}\n", "value")

                    w.insert(tk.END, "• Edad Estimada: ", "label")
                    w.insert(tk.END, f"{p['age_range']}\n", "value")

                    w.insert(tk.END, "• Estatus Socioeconómico (Visual): \n  ", "label")
                    w.insert(tk.END, f"{p['economic_profile']}\n", "econ")

                    w.insert(tk.END, "• Apariencia Física: \n  ", "label")
                    w.insert(tk.END, f"{p['physical_desc']}\n", "value")

                    w.insert(tk.END, "• Vestimenta y Estilo: \n  ", "label")
                    w.insert(tk.END, f"{p['style_desc']} (Color: {p['upper_color']})\n", "value")

                    w.insert(tk.END, "• Expresión y Postura: \n  ", "label")
                    w.insert(tk.END, f"{p['expression']} | {p['posture']}\n", "value")

                    w.insert(tk.END, "• Interacción Portal: \n  ", "label")
                    w.insert(tk.END, f"Mirada: {p['eye_contact']} | Saludo: {p['waving']}\n", "value")

                    w.insert(tk.END, "• Moderación Actos/Señas: ", "label")
                    w.insert(tk.END, f"{p['offensive']}\n", "alert" if is_off else "value")

                    w.insert(tk.END, "─" * 38 + "\n\n", "label")

            w.configure(state=tk.DISABLED)

    def swap_cameras(self):
        ip1, ip2 = self.ip1_var.get(), self.ip2_var.get()
        mac1, mac2 = self.mac1_var.get(), self.mac2_var.get()
        p1, p2 = self.pass1_var.get(), self.pass2_var.get()
        self.ip1_var.set(ip2)
        self.ip2_var.set(ip1)
        self.mac1_var.set(mac2)
        self.mac2_var.set(mac1)
        self.pass1_var.set(p2)
        self.pass2_var.set(p1)
        if self.running:
            self.start_unified_signal()
        self.status_var.set(f"🔄 Roles intercambiados: Cam 1 ahora es {ip2} y Cam 2 es {ip1}")

    def scan_both_cameras(self):
        self.status_var.set("Sondeando cámaras Dahua en el switch...")
        threading.Thread(target=self._discover_and_diagnose_cameras, daemon=True).start()

    def _discover_and_diagnose_cameras(self):
        devices = []
        body = b'{"method":"DHDiscover.search","params":{"mac":"","uni":1}}\n'
        ln = len(body)
        msg = struct.pack("<I4sIIIII", 32, b"DHIP", 0, 0, ln, 0, ln) + b"\x00\x00\x00\x00" + body

        for bind_ip in ["192.168.1.250", "0.0.0.0"]:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.settimeout(1.5)
                try:
                    s.bind((bind_ip, 37810))
                except Exception:
                    s.bind((bind_ip, 0))
                s.sendto(msg, ("255.255.255.255", 37810))
                s.sendto(msg, ("192.168.1.255", 37810))
                s.sendto(msg, ("239.255.255.251", 37810))
                while True:
                    data, addr = s.recvfrom(65535)
                    if len(data) > 40:
                        raw_json = data[32:].decode("utf-8", errors="ignore").strip("\x00 \r\n")
                        try:
                            obj = json.loads(raw_json)
                            mac = obj.get("mac", "").lower().replace(":", "-")
                            dev_info = obj.get("params", {}).get("deviceInfo", {})
                            ip = dev_info.get("IPv4Address", {}).get("IPAddress", addr[0])
                            serial = dev_info.get("SerialNo", "")
                            model = dev_info.get("DeviceType", "IPC")
                            if mac and not any(d["mac"] == mac for d in devices):
                                devices.append({"ip": ip, "mac": mac, "serial": serial, "model": model})
                        except Exception:
                            pass
            except Exception:
                pass
            finally:
                try:
                    s.close()
                except Exception:
                    pass
            if len(devices) >= 2:
                break

        self.root.after(0, lambda: self._apply_discovered_devices(devices))

    def _apply_discovered_devices(self, devices: list):
        if len(devices) >= 2:
            devices.sort(key=lambda d: 0 if d["ip"].endswith(".109") else 1)
            c1, c2 = devices[0], devices[1]
            self.ip1_var.set(c1["ip"])
            self.mac1_var.set(c1["mac"])
            self.ip2_var.set(c2["ip"])
            self.mac2_var.set(c2["mac"])
            if c1["ip"] != c2["ip"]:
                self.net_status_var.set(f"✅ 2 Cámaras Activas ({c1['ip']} y {c2['ip']})")
                self.net_badge.configure(fg="#4ade80")
            else:
                self.net_status_var.set(f"⚠️ Conflicto IP en {c1['ip']}")
                self.net_badge.configure(fg="#fbbf24")
        elif len(devices) == 1:
            d = devices[0]
            self.net_status_var.set(f"✅ Cámara en {d['ip']}")
            self.net_badge.configure(fg="#4ade80")

    def open_dual_ip_wizard(self):
        win = tk.Toplevel(self.root)
        win.title("🛠️ Gestión de Red y Activación de 2da Cámara")
        win.geometry("620x340")
        win.configure(bg="#161b26", padx=18, pady=14)
        win.transient(self.root)
        win.grab_set()

        tk.Label(
            win,
            text="Estado de tus 2 Cámaras en el Switch PoE:",
            bg="#161b26",
            fg="#4ade80",
            font=("Segoe UI", 11, "bold"),
        ).pack(anchor="w", pady=(0, 8))

        txt = (
            f"• Cámara en {self.ip1_var.get()} (MAC {self.mac1_var.get()}): Lista.\n"
            f"• Cámara en {self.ip2_var.get()} (MAC {self.mac2_var.get()}): Lista.\n\n"
            "Pulsa el botón verde para asegurar el vínculo físico ARP de Windows hacia ambas cámaras:"
        )
        tk.Label(win, text=txt, bg="#161b26", fg="#e2e8f0", justify=tk.LEFT, font=("Segoe UI", 9)).pack(anchor="w", pady=(0, 12))

        tk.Button(
            win,
            text="⚡ 1. Actualizar Vínculo de Red (ARP) para ambas cámaras (.108 y .109)",
            command=self._fix_both_arp_entries,
            bg="#16a34a",
            fg="white",
            relief=tk.FLAT,
            font=("Segoe UI", 9, "bold"),
            pady=7,
            cursor="hand2",
        ).pack(fill=tk.X, pady=(0, 8))

        tk.Button(
            win,
            text="🌐 2. Abrir Panel Web de 192.168.1.108 (Inicializar clave si es cámara nueva)",
            command=lambda: os.startfile("http://192.168.1.108"),
            bg="#0284c7",
            fg="white",
            relief=tk.FLAT,
            font=("Segoe UI", 9, "bold"),
            pady=7,
            cursor="hand2",
        ).pack(fill=tk.X, pady=(0, 8))

        tk.Button(
            win,
            text="🌐 3. Abrir Panel Web de 192.168.1.109",
            command=lambda: os.startfile("http://192.168.1.109"),
            bg="#334155",
            fg="white",
            relief=tk.FLAT,
            font=("Segoe UI", 9),
            pady=6,
            cursor="hand2",
        ).pack(fill=tk.X)

    def _fix_both_arp_entries(self):
        cmd = (
            'netsh interface ipv4 delete neighbors "Ethernet" 192.168.1.108 & '
            'netsh interface ipv4 set neighbors "Ethernet" 192.168.1.108 98-f9-cc-95-db-ec & '
            'netsh interface ipv4 set neighbors "Ethernet" 192.168.1.109 98-f9-cc-95-db-bd'
        )
        ctypes.windll.shell32.ShellExecuteW(None, "runas", "cmd.exe", f"/c {cmd}", None, 0)
        self.status_var.set("✅ Tabla ARP actualizada: .108 -> db-ec y .109 -> db-bd")

    def _disable_cameras_osd_async(self):
        """
        Desactiva directamente en el firmware de ambas cámaras Dahua los textos OSD de Fecha/Hora
        ('2000-01-01...') y el Nombre de Cámara ('IPC') para que la señal sea un espejo limpio.
        """
        def _worker():
            for ip, user, pwd in [
                (self.ip1_var.get().strip(), self.user1_var.get().strip(), self.pass1_var.get()),
                (self.ip2_var.get().strip(), self.user2_var.get().strip(), self.pass2_var.get()),
            ]:
                if not ip:
                    continue
                try:
                    requests.get(
                        f"http://{ip}/cgi-bin/configManager.cgi?action=setConfig"
                        "&VideoWidget[0].TimeTitle.EncodeBlend=false"
                        "&VideoWidget[0].TimeTitle.PreviewBlend=false"
                        "&VideoWidget[0].ChannelTitle.EncodeBlend=false"
                        "&VideoWidget[0].ChannelTitle.PreviewBlend=false",
                        auth=HTTPDigestAuth(user, pwd),
                        timeout=2.5,
                    )
                except Exception:
                    pass

        threading.Thread(target=_worker, daemon=True).start()

    def open_clean_portal_call_window(self, borderless_fullscreen: bool = True):
        """
        Abre la Ventana Independiente 'Portal de Llamada Perpetua 24/7' en Fullscreen SIN BORDES,
        sin barra de navegación, sin barra de título y con ocultamiento automático del cursor.
        - Muestra ÚNICAMENTE la imagen limpia y los movimientos/blureos configurados por el Panel Admin.
        - Si el administrador conecta mouse/teclado:
          * Mover el mouse muestra el cursor temporalmente.
          * Pulsar F12, Ctrl+A, Tab, Esc o Clic Derecho abre el Panel de Administrador encima para configurar.
          * Pulsar F11 o Doble Clic alterna Fullscreen sin bordes.
        """
        if self.portal_call_win is not None and self.portal_call_win.winfo_exists():
            if borderless_fullscreen:
                self._apply_borderless_fullscreen(self.portal_call_win, True)
            self.portal_call_win.lift()
            return

        win = tk.Toplevel(self.root)
        win.title("Portal 24/7")
        sw = win.winfo_screenwidth()
        sh = win.winfo_screenheight()
        win.geometry(f"{sw}x{sh}+0+0")
        win.configure(bg="#000000", highlightthickness=0, bd=0)

        lbl = tk.Label(
            win,
            bg="#000000",
            bd=0,
            highlightthickness=0,
            text="",
        )
        lbl.pack(fill=tk.BOTH, expand=True)

        self._cursor_hide_job = None

        def _hide_cursor():
            if win.winfo_exists() and self.portal_call_fullscreen:
                try:
                    win.config(cursor="none")
                except Exception:
                    pass

        def _on_mouse_motion(event=None):
            if not win.winfo_exists():
                return
            try:
                win.config(cursor="arrow")
            except Exception:
                pass
            if self._cursor_hide_job is not None:
                try:
                    win.after_cancel(self._cursor_hide_job)
                except Exception:
                    pass
            self._cursor_hide_job = win.after(2500, _hide_cursor)

        def _toggle_fullscreen(event=None):
            self._apply_borderless_fullscreen(win, not self.portal_call_fullscreen)

        def _open_admin_with_peripherals(event=None):
            # Si el administrador pulsa F12, Esc, Ctrl+A o Clic Derecho con periféricos conectados,
            # desactiva overrideredirect temporalmente para que pueda usar el Panel Admin encima cómodamente
            try:
                win.overrideredirect(False)
                win.attributes("-topmost", False)
            except Exception:
                pass
            self.show_admin_panel()

        win.bind("<Motion>", _on_mouse_motion)
        lbl.bind("<Motion>", _on_mouse_motion)
        self._bind_kinect_mouse_assist(lbl, extra_motion_cb=_on_mouse_motion)
        win.bind("<F11>", _toggle_fullscreen)
        lbl.bind("<Double-Button-1>", _toggle_fullscreen)
        win.bind("<F12>", _open_admin_with_peripherals)
        win.bind("<Escape>", _open_admin_with_peripherals)
        win.bind("<Control-a>", _open_admin_with_peripherals)
        win.bind("<Control-A>", _open_admin_with_peripherals)
        lbl.bind("<Button-3>", _open_admin_with_peripherals)

        self.portal_call_win = win
        self.portal_call_lbl = lbl

        self._apply_borderless_fullscreen(win, borderless_fullscreen)
        _on_mouse_motion()

        if not self.running:
            self.start_unified_signal()

    def _bind_kinect_mouse_assist(self, widget: tk.Widget, extra_motion_cb=None):
        """Permite probar o interactuar con los botones/juegos Kinect también con mouse (Clic Izquierdo = Pellizco)."""
        state_pressed = {"down": False}

        def _push_pos(event):
            if extra_motion_cb is not None:
                try:
                    extra_motion_cb(event)
                except Exception:
                    pass
            try:
                ww = max(1, widget.winfo_width())
                wh = max(1, widget.winfo_height())
                nx = max(0.0, min(1.0, float(event.x) / float(ww)))
                ny = max(0.0, min(1.0, float(event.y) / float(wh)))
                self.kinect_engine.set_mouse_cursor(nx, ny, state_pressed["down"])
            except Exception:
                pass

        def _on_down(event):
            state_pressed["down"] = True
            _push_pos(event)

        def _on_up(event):
            state_pressed["down"] = False
            _push_pos(event)

        widget.bind("<Motion>", _push_pos)
        widget.bind("<ButtonPress-1>", _on_down)
        widget.bind("<B1-Motion>", _push_pos)
        widget.bind("<ButtonRelease-1>", _on_up)

    def _apply_borderless_fullscreen(self, win: tk.Toplevel, enable: bool):
        self.portal_call_fullscreen = bool(enable)
        if not win.winfo_exists():
            return
        sw = win.winfo_screenwidth()
        sh = win.winfo_screenheight()
        try:
            if enable:
                win.overrideredirect(True)
                win.geometry(f"{sw}x{sh}+0+0")
                win.attributes("-fullscreen", True)
                win.attributes("-topmost", True)
                win.focus_force()
            else:
                win.attributes("-topmost", False)
                win.attributes("-fullscreen", False)
                win.overrideredirect(False)
                win.geometry("1280x720+40+40")
                win.config(cursor="arrow")
        except Exception:
            try:
                win.attributes("-fullscreen", enable)
            except Exception:
                pass

    def open_p2p_and_devices_config_window(self):
        """
        Abre el Panel Administrador de Llamada Perpetua 24/7 P2P, Compatibilidad Universal de Cámara
        (Dahua / USB Webcam / RTSP Universal) y Configuración de Micrófono y Parlante (Altavoz)
        con diseño React Glassmorphism. Todo se guarda automáticamente en config_portal.json.
        """
        if self.p2p_config_win is not None and self.p2p_config_win.winfo_exists():
            self.p2p_config_win.lift()
            return

        win = tk.Toplevel(self.root)
        win.title("🌐 React Glass Admin — Llamada Perpetua 24/7 P2P, Cámara Universal y Parlante")
        win.geometry("700x760")
        win.configure(bg=self.theme["bg_app"], padx=18, pady=14)
        win.transient(self.root)

        # --- CARD A: RED P2P Y AUTO-UNIÓN 24/7 ---
        p2p_frame = self._create_glass_card(
            win,
            "🔗 1. Llamada Perpetua 24/7 en Red Local P2P",
            badge="AUTO-DISCOVERY LAN",
            accent=self.theme["emerald"],
            pady_outer=10,
        )

        self._add_checkbox(
            p2p_frame,
            "🔗 Auto-Unir a Llamada Perpetua 24/7 al ejecutar el programa (Se recuerda automáticamente)",
            self.auto_join_call_var,
            fg="#34d399",
            bg=self.theme["bg_card"],
            bold=True,
        )

        self._add_checkbox(
            p2p_frame,
            "🪞 Abrir automáticamente la ventana limpia 'Portal Llamada 24/7' en Fullscreen sin bordes",
            self.auto_open_portal_win_var,
            fg="#e2e8f0",
            bg=self.theme["bg_card"],
        )

        self._add_checkbox(
            p2p_frame,
            "🖼️ Incluir recuadro PiP limpio de cámara propia en esquina durante llamada con otro equipo",
            self.portal_call_pip_self_var,
            fg="#cbd5e1",
            bg=self.theme["bg_card"],
        )

        peer_row = tk.Frame(p2p_frame, bg=self.theme["bg_card"])
        peer_row.pack(fill=tk.X, pady=(5, 3))
        tk.Label(peer_row, text="Equipo Destino en Red P2P:", bg=self.theme["bg_card"], fg="#cbd5e1", font=("Segoe UI", 8, "bold")).pack(side=tk.LEFT)

        peer_cb = ttk.Combobox(
            peer_row,
            textvariable=self.target_peer_ip_var,
            values=self._get_peer_combobox_values(),
            width=40,
        )
        peer_cb.pack(side=tk.LEFT, padx=8, fill=tk.X, expand=True)

        def _refresh_peers_ui():
            peer_cb.configure(values=self._get_peer_combobox_values())
            self._sync_p2p_and_audio_settings()
            if not self.p2p_node.in_call:
                peers = self.p2p_node.get_peers_list()
                if peers:
                    self.p2p_node.connect_to_peer(peers[0]["ip"], peers[0]["video_port"], f"{peers[0]['hostname']} ({peers[0]['ip']})")
                elif self.p2p_node.target_peer_ip:
                    self.p2p_node.connect_to_peer(self.p2p_node.target_peer_ip)

        self._create_react_button(
            peer_row,
            "🔄 Unir / Actualizar Red",
            _refresh_peers_ui,
            variant="cyan",
            pady=4,
            padx=10,
        ).pack(side=tk.RIGHT)

        view_row = tk.Frame(p2p_frame, bg=self.theme["bg_card"])
        view_row.pack(fill=tk.X, pady=(3, 3))
        tk.Label(view_row, text="Imagen mostrada en Portal 24/7:", bg=self.theme["bg_card"], fg="#cbd5e1", font=("Segoe UI", 8, "bold")).pack(side=tk.LEFT)
        ttk.Combobox(
            view_row,
            textvariable=self.portal_call_view_mode_var,
            values=[
                "Automático 24/7 (Muestra Par Remoto en Llamada / Espejo Local en Espera)",
                "Solo Señal Local Procesada (Modo Espejo + Movimientos IA + Blureo)",
                "Solo Señal Remota P2P Recibida",
                "Pantalla Dividida P2P (Equipo Remoto Izq + Equipo Local Der)",
            ],
            state="readonly",
            width=54,
        ).pack(side=tk.RIGHT)

        tk.Label(
            p2p_frame,
            textvariable=self.p2p_status_var,
            bg=self.theme["bg_input"],
            fg="#38bdf8",
            font=("Segoe UI", 8, "bold"),
            pady=5,
            highlightbackground=self.theme["border_subtle"],
            highlightthickness=1,
        ).pack(fill=tk.X, pady=(4, 0))

        # --- CARD B: COMPATIBILIDAD CON CUALQUIER CÁMARA ---
        cam_frame = self._create_glass_card(
            win,
            "📷 2. Compatibilidad Universal de Cámara",
            badge="DAHUA • USB • RTSP/ONVIF",
            accent=self.theme["amber"],
            pady_outer=10,
        )

        mode_r = tk.Frame(cam_frame, bg=self.theme["bg_card"])
        mode_r.pack(fill=tk.X, pady=3)
        tk.Label(mode_r, text="Modo de Entrada de Cámara:", bg=self.theme["bg_card"], fg="#e2e8f0", font=("Segoe UI", 8, "bold")).pack(side=tk.LEFT)
        ttk.Combobox(
            mode_r,
            textvariable=self.camera_source_mode_var,
            values=[
                "Dahua Dual / Simple PoE (RTSP Automático)",
                "Cámara USB / Webcam Local (Universal)",
                "Cámara IP Universal (URL RTSP / HTTP / ONVIF)",
            ],
            state="readonly",
            width=46,
        ).pack(side=tk.RIGHT)

        usb_r = tk.Frame(cam_frame, bg=self.theme["bg_card"])
        usb_r.pack(fill=tk.X, pady=3)
        tk.Label(usb_r, text="Cámara USB Principal:", bg=self.theme["bg_card"], fg="#cbd5e1", font=("Segoe UI", 8)).pack(side=tk.LEFT)
        usb1_cb = ttk.Combobox(
            usb_r,
            textvariable=self.usb_cam1_var,
            values=[d["label"] for d in self.usb_cameras_list],
            width=34,
        )
        usb1_cb.pack(side=tk.LEFT, padx=8)

        def _scan_usb():
            self.usb_cameras_list = discover_usb_cameras(4)
            labels = [d["label"] for d in self.usb_cameras_list]
            usb1_cb.configure(values=labels)
            usb2_cb.configure(values=["Desactivada (Solo 1 Cámara USB)"] + labels)
            if labels:
                self.usb_cam1_var.set(labels[0])

        self._create_react_button(
            usb_r,
            "🔍 Detectar Cámaras USB",
            _scan_usb,
            variant="glass",
            pady=4,
            padx=10,
        ).pack(side=tk.RIGHT)

        usb2_r = tk.Frame(cam_frame, bg=self.theme["bg_card"])
        usb2_r.pack(fill=tk.X, pady=3)
        tk.Label(usb2_r, text="2da Cámara USB (Opcional):", bg=self.theme["bg_card"], fg="#cbd5e1", font=("Segoe UI", 8)).pack(side=tk.LEFT)
        usb2_cb = ttk.Combobox(
            usb2_r,
            textvariable=self.usb_cam2_var,
            values=["Desactivada (Solo 1 Cámara USB)"] + [d["label"] for d in self.usb_cameras_list],
            width=46,
        )
        usb2_cb.pack(side=tk.RIGHT)

        rtsp1_r = tk.Frame(cam_frame, bg=self.theme["bg_card"])
        rtsp1_r.pack(fill=tk.X, pady=3)
        tk.Label(rtsp1_r, text="URL Cámara Universal 1 (RTSP/HTTP):", bg=self.theme["bg_card"], fg="#cbd5e1", font=("Segoe UI", 8)).pack(side=tk.LEFT)
        tk.Entry(
            rtsp1_r,
            textvariable=self.custom_rtsp1_var,
            bg=self.theme["bg_input"],
            fg="#f8fafc",
            insertbackground="#38bdf8",
            relief=tk.FLAT,
            highlightbackground=self.theme["border_glass"],
            highlightcolor="#38bdf8",
            highlightthickness=1,
            font=("Segoe UI", 8),
        ).pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(8, 0))

        rtsp2_r = tk.Frame(cam_frame, bg=self.theme["bg_card"])
        rtsp2_r.pack(fill=tk.X, pady=3)
        tk.Label(rtsp2_r, text="URL Cámara Universal 2 (Opcional):", bg=self.theme["bg_card"], fg="#cbd5e1", font=("Segoe UI", 8)).pack(side=tk.LEFT)
        tk.Entry(
            rtsp2_r,
            textvariable=self.custom_rtsp2_var,
            bg=self.theme["bg_input"],
            fg="#f8fafc",
            insertbackground="#38bdf8",
            relief=tk.FLAT,
            highlightbackground=self.theme["border_glass"],
            highlightcolor="#38bdf8",
            highlightthickness=1,
            font=("Segoe UI", 8),
        ).pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(8, 0))

        # --- CARD C: FUNCIÓN DE CÁMARA MICRÓFONO Y PARLANTE (ALTAVOZ) ---
        aud_frame = self._create_glass_card(
            win,
            "🔊 3. Configuración de Parlante (Altavoz) y Micrófono",
            badge="FULL-DUPLEX AUDIO",
            accent=self.theme["violet"],
            pady_outer=10,
        )

        spk_r = tk.Frame(aud_frame, bg=self.theme["bg_card"])
        spk_r.pack(fill=tk.X, pady=2)
        tk.Checkbutton(
            spk_r,
            text="🔊 Activar Parlante (Altavoz):",
            variable=self.enable_speaker_var,
            bg=self.theme["bg_card"],
            fg="#34d399",
            selectcolor=self.theme["bg_input"],
            activebackground=self.theme["bg_card"],
            font=("Segoe UI", 8, "bold"),
        ).pack(side=tk.LEFT)
        spk_cb = ttk.Combobox(
            spk_r,
            textvariable=self.speaker_device_var,
            values=[d["label"] for d in self.audio_outputs_list],
            state="readonly",
            width=46,
        )
        spk_cb.pack(side=tk.RIGHT)

        mic_r = tk.Frame(aud_frame, bg=self.theme["bg_card"])
        mic_r.pack(fill=tk.X, pady=2)
        tk.Checkbutton(
            mic_r,
            text="🎤 Activar Micrófono (Voz):",
            variable=self.enable_mic_var,
            bg=self.theme["bg_card"],
            fg="#38bdf8",
            selectcolor=self.theme["bg_input"],
            activebackground=self.theme["bg_card"],
            font=("Segoe UI", 8, "bold"),
        ).pack(side=tk.LEFT)
        mic_cb = ttk.Combobox(
            mic_r,
            textvariable=self.mic_device_var,
            values=[d["label"] for d in self.audio_inputs_list],
            state="readonly",
            width=46,
        )
        mic_cb.pack(side=tk.RIGHT)

        vol_r = tk.Frame(aud_frame, bg=self.theme["bg_card"])
        vol_r.pack(fill=tk.X, pady=(4, 2))
        tk.Label(vol_r, text="Volumen de Parlante:", bg=self.theme["bg_card"], fg="#cbd5e1", font=("Segoe UI", 8)).pack(side=tk.LEFT)
        tk.Scale(
            vol_r,
            from_=0.0,
            to=2.0,
            resolution=0.05,
            orient=tk.HORIZONTAL,
            variable=self.speaker_volume_var,
            bg=self.theme["bg_card"],
            fg="#34d399",
            activebackground="#10b981",
            bd=0,
            sliderrelief=tk.FLAT,
            highlightthickness=0,
            troughcolor=self.theme["bg_input"],
            length=170,
        ).pack(side=tk.LEFT, padx=8)

        tk.Label(vol_r, text="Ganancia Mic:", bg=self.theme["bg_card"], fg="#cbd5e1", font=("Segoe UI", 8)).pack(side=tk.LEFT)
        tk.Scale(
            vol_r,
            from_=0.2,
            to=2.5,
            resolution=0.05,
            orient=tk.HORIZONTAL,
            variable=self.mic_gain_var,
            bg=self.theme["bg_card"],
            fg="#38bdf8",
            activebackground="#38bdf8",
            bd=0,
            sliderrelief=tk.FLAT,
            highlightthickness=0,
            troughcolor=self.theme["bg_input"],
            length=150,
        ).pack(side=tk.LEFT, padx=6)

        aud_btns = tk.Frame(aud_frame, bg=self.theme["bg_card"])
        aud_btns.pack(fill=tk.X, pady=(6, 0))

        def _refresh_audio_devs():
            self.audio_inputs_list, self.audio_outputs_list = AudioStreamEngine.list_audio_devices()
            mic_cb.configure(values=[d["label"] for d in self.audio_inputs_list])
            spk_cb.configure(values=[d["label"] for d in self.audio_outputs_list])

        self._create_react_button(
            aud_btns,
            "🔔 Probar Parlante (Tono de Prueba)",
            lambda: self.p2p_node.audio_engine.play_test_tone(),
            variant="violet",
            pady=5,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))

        self._create_react_button(
            aud_btns,
            "🔄 Refrescar Micrófonos y Parlantes",
            _refresh_audio_devs,
            variant="glass",
            pady=5,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 0))

        # --- BOTONES INFERIORES DE APLICACIÓN ---
        bottom_row = tk.Frame(win, bg=self.theme["bg_app"])
        bottom_row.pack(fill=tk.X, pady=(4, 0))

        def _apply_and_restart_signal():
            self._sync_p2p_and_audio_settings()
            self._save_persistent_config()
            self.start_unified_signal()
            if self.auto_open_portal_win_var.get():
                self.open_clean_portal_call_window(borderless_fullscreen=True)

        self._create_react_button(
            bottom_row,
            "✅ Guardar, Aplicar Cámara/Parlante y Abrir Portal 24/7 Fullscreen",
            _apply_and_restart_signal,
            variant="emerald",
            pady=7,
            font_size=9,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))

        self._create_react_button(
            bottom_row,
            "Cerrar",
            win.destroy,
            variant="glass",
            pady=7,
            padx=18,
            font_size=9,
        ).pack(side=tk.RIGHT)

        self.p2p_config_win = win

    def _get_peer_combobox_values(self) -> list:
        vals = ["Auto-Descubrimiento LAN P2P (24/7)"]
        for p in self.p2p_node.get_peers_list():
            vals.append(f"{p['hostname']} ({p['ip']})")
        return vals

    def start_unified_signal(self):
        if self.running:
            self.stop_unified_signal()
            time.sleep(0.15)

        cam_mode = self.camera_source_mode_var.get()
        if cam_mode.startswith("Dahua"):
            self._disable_cameras_osd_async()

        self.running = True
        self.btn_connect.configure(state=tk.DISABLED)
        self.btn_disconnect.configure(state=tk.NORMAL)

        if cam_mode.startswith("Cámara USB"):
            usb1_lbl = self.usb_cam1_var.get()
            usb1_idx = "0"
            for ch in usb1_lbl.split():
                if ch.isdigit():
                    usb1_idx = ch
                    break
            self.grabber1 = LatestFrameGrabber(
                f"USB:{usb1_idx}",
                ip_hint="",
                mirror_fn=lambda: self.mirror_mode_var.get(),
                source_type="usb",
            )
            usb2_lbl = self.usb_cam2_var.get()
            if self.enable_cam2_var.get() and not usb2_lbl.startswith("Desactivada"):
                usb2_idx = "1"
                for ch in usb2_lbl.split():
                    if ch.isdigit():
                        usb2_idx = ch
                        break
                self.grabber2 = LatestFrameGrabber(
                    f"USB:{usb2_idx}",
                    ip_hint="",
                    mirror_fn=lambda: self.mirror_mode_var.get(),
                    source_type="usb",
                )
            else:
                self.grabber2 = None
            self.status_var.set(f"⚡ Cámara USB Local ({usb1_lbl}) + Red P2P 24/7 Activa...")

        elif cam_mode.startswith("Cámara IP Universal"):
            url1 = self.custom_rtsp1_var.get().strip()
            self.grabber1 = LatestFrameGrabber(
                url1,
                ip_hint="",
                mirror_fn=lambda: self.mirror_mode_var.get(),
                source_type="rtsp",
            )
            url2 = self.custom_rtsp2_var.get().strip()
            if self.enable_cam2_var.get() and url2:
                self.grabber2 = LatestFrameGrabber(
                    url2,
                    ip_hint="",
                    mirror_fn=lambda: self.mirror_mode_var.get(),
                    source_type="rtsp",
                )
            else:
                self.grabber2 = None
            self.status_var.set("⚡ Cámara IP Universal (RTSP/HTTP) + Red P2P 24/7 Activa...")

        else:
            subtype = "0" if self.stream_quality_var.get().startswith("0") else "1"
            ip1 = self.ip1_var.get().strip()
            u1, p1 = self.user1_var.get().strip(), self.pass1_var.get()
            url1 = f"rtsp://{u1}:{p1}@{ip1}:554/cam/realmonitor?channel=1&subtype={subtype}"
            self.grabber1 = LatestFrameGrabber(url1, ip1, mirror_fn=lambda: self.mirror_mode_var.get())

            ip2 = self.ip2_var.get().strip()
            u2, p2 = self.user2_var.get().strip(), self.pass2_var.get()
            url2 = f"rtsp://{u2}:{p2}@{ip2}:554/cam/realmonitor?channel=1&subtype={subtype}"
            self.grabber2 = LatestFrameGrabber(url2, ip2, mirror_fn=lambda: self.mirror_mode_var.get())
            self.status_var.set("⚡ Modo Espejo Limpio TURBO ZERO-LAG + Llamada P2P 24/7 Activo...")

        threading.Thread(target=self._ai_background_worker, daemon=True).start()
        threading.Thread(target=self._compositor_loop, daemon=True).start()

    def _ai_background_worker(self):
        last_seq1 = -1
        last_seq2 = -1

        while self.running:
            if not self.ai_enabled_var.get():
                time.sleep(0.2)
                continue

            did_work = False

            if self.grabber1 is not None:
                f1, seq1, ok1 = self.grabber1.get_latest()
                if ok1 and f1 is not None and seq1 != last_seq1:
                    last_seq1 = seq1
                    self.ai_cam1.analyze_async(f1)
                    did_work = True

            if self.grabber2 is not None and self.enable_cam2_var.get():
                f2, seq2, ok2 = self.grabber2.get_latest()
                if ok2 and f2 is not None and seq2 != last_seq2:
                    last_seq2 = seq2
                    self.ai_cam2.analyze_async(f2)
                    did_work = True

            if not did_work:
                time.sleep(0.03)
            else:
                time.sleep(0.01)

    def _compositor_loop(self):
        self.frame_count = 0
        self.last_fps_time = time.time()
        canvas_w, canvas_h = 1280, 720

        while self.running:
            t_start = time.time()

            raw1, _, ok1 = self.grabber1.get_latest() if self.grabber1 else (None, 0, False)
            raw2, _, ok2 = (
                self.grabber2.get_latest()
                if (self.grabber2 and self.enable_cam2_var.get())
                else (None, 0, False)
            )
            remote_frame, _, in_p2p_call = self.p2p_node.get_remote_video_frame()

            if raw1 is None and raw2 is None and not (in_p2p_call and remote_frame is not None):
                time.sleep(0.02)
                continue

            ovl = self.show_overlays_var.get()
            aframe = self.auto_frame_var.get()
            max_z = float(self.max_zoom_var.get())
            omega = float(self.smooth_omega_var.get())
            enh = self.enhance_quality_var.get()
            obs_blur = self.obstruction_blur_var.get()
            off_blur = self.offensive_blur_var.get()
            full_blur = self.blur_full_person_var.get()
            patrol = self.idle_patrol_var.get()

            p1, p2 = None, None
            s1 = {"persons": 0, "eye_contact": 0, "waving": 0, "offensive": 0, "zoom": 1.0, "motion_pct": 0.0, "mode": "SIN SEÑAL CAM 1", "engaged": False, "profiles": []}
            s2 = {"persons": 0, "eye_contact": 0, "waving": 0, "offensive": 0, "zoom": 1.0, "motion_pct": 0.0, "mode": "SIN SEÑAL CAM 2", "engaged": False, "profiles": []}

            if raw1 is not None:
                p1, s1 = self.ai_cam1.render_fast(
                    raw1,
                    enable_ai_overlay=ovl,
                    enable_auto_framing=aframe,
                    max_digital_zoom=max_z,
                    smooth_omega=omega,
                    enhance_quality=enh,
                    obstruction_blur=obs_blur,
                    enable_offensive_blur=off_blur,
                    blur_full_person_on_offense=full_blur,
                    enable_idle_patrol=patrol,
                    target_size=(canvas_w, canvas_h),
                )

            if raw2 is not None and self.enable_cam2_var.get():
                p2, s2 = self.ai_cam2.render_fast(
                    raw2,
                    enable_ai_overlay=ovl,
                    enable_auto_framing=aframe,
                    max_digital_zoom=max_z,
                    smooth_omega=omega,
                    enhance_quality=enh,
                    obstruction_blur=obs_blur,
                    enable_offensive_blur=off_blur,
                    blur_full_person_on_offense=full_blur,
                    enable_idle_patrol=patrol,
                    target_size=(canvas_w, canvas_h),
                )

            mode_sel = self.signal_mode_var.get()
            cam1_engaged = s1.get("engaged", False)
            cam2_engaged = s2.get("engaged", False)
            engaged_any = cam1_engaged or cam2_engaged
            hide_pip = bool(self.hide_inactive_cam_pip_var.get())

            if p1 is not None and p2 is not None and self.enable_cam2_var.get():
                if mode_sel.startswith("Auto-Director"):
                    # Si el foco está en la cámara 1 a 1 (Cam 2), ocultar la panorámica;
                    # si el foco vuelve a campo abierto / panorámica (Cam 1), ocultar la otra cámara.
                    if cam2_engaged:
                        target_blend = 1.0
                    elif cam1_engaged and s2.get("persons", 0) == 0:
                        target_blend = 0.0
                    else:
                        target_blend = 1.0 if engaged_any else 0.0

                    self.cam2_blend += (target_blend - self.cam2_blend) * 0.035
                    if self.cam2_blend <= 0.01:
                        composed = p1
                    elif self.cam2_blend >= 0.99:
                        composed = p2
                    else:
                        composed = cv2.addWeighted(p2, self.cam2_blend, p1, 1.0 - self.cam2_blend, 0)

                    # Ocultar vista panorámica si el foco está en la otra y viceversa (sin PiP distractor)
                    if not hide_pip:
                        pip_src = p1 if self.cam2_blend > 0.5 else p2
                        composed = self._overlay_pip(composed, pip_src)

                elif mode_sel.startswith("Portal Dual"):
                    if hide_pip:
                        # Si está activo ocultar la cámara sin foco, muestra únicamente la cámara con foco activo
                        if cam1_engaged and not cam2_engaged:
                            composed = p1.copy()
                        else:
                            composed = p2.copy()
                    else:
                        composed = p2.copy()
                        composed = self._overlay_pip(composed, p1)

                elif mode_sel.startswith("Pantalla Dividida"):
                    composed = np.zeros((canvas_h, canvas_w, 3), dtype=np.uint8)
                    half_w = canvas_w // 2
                    composed[:, :half_w] = p1[:, canvas_w // 4 : canvas_w // 4 + half_w]
                    composed[:, half_w:] = p2[:, canvas_w // 4 : canvas_w // 4 + half_w]
                    cv2.line(composed, (half_w, 0), (half_w, canvas_h), (56, 189, 248), 2)

                elif mode_sel.startswith("Solo Cámara 2"):
                    composed = p2
                else:
                    composed = p1

            elif p1 is not None:
                composed = p1
            elif p2 is not None:
                composed = p2
            else:
                composed = None

            if composed is not None and self.circular_portal_var.get():
                composed = self._apply_portal_circle_mask(composed, engaged_any)

            # Enviar nuestra señal limpia procesada (Espejo, Movimientos IA y Blureo) a la red P2P 24/7
            if composed is not None:
                self.p2p_node.send_local_video_frame(composed)

            # Construir el cuadro base para la ventana 'Portal Llamada 24/7'
            pview_mode = self.portal_call_view_mode_var.get()
            rem_resized = None
            if in_p2p_call and remote_frame is not None:
                if remote_frame.shape[1] != canvas_w or remote_frame.shape[0] != canvas_h:
                    rem_resized = cv2.resize(remote_frame, (canvas_w, canvas_h), interpolation=cv2.INTER_NEAREST)
                else:
                    rem_resized = remote_frame

            if pview_mode.startswith("Solo Señal Local") or rem_resized is None:
                portal_call_frame = composed.copy() if composed is not None else rem_resized
            elif pview_mode.startswith("Solo Señal Remota"):
                portal_call_frame = rem_resized.copy()
            elif pview_mode.startswith("Pantalla Dividida P2P") and composed is not None:
                portal_call_frame = np.zeros((canvas_h, canvas_w, 3), dtype=np.uint8)
                half_w = canvas_w // 2
                portal_call_frame[:, :half_w] = rem_resized[:, canvas_w // 4 : canvas_w // 4 + half_w]
                portal_call_frame[:, half_w:] = composed[:, canvas_w // 4 : canvas_w // 4 + half_w]
                cv2.line(portal_call_frame, (half_w, 0), (half_w, canvas_h), (56, 189, 248), 2)
            else:
                # Automático 24/7: Muestra al equipo remoto en llamada (con PiP propio opcional) o Espejo Local en espera
                portal_call_frame = rem_resized.copy()
                if self.portal_call_pip_self_var.get() and composed is not None and not hide_pip:
                    portal_call_frame = self._overlay_pip(portal_call_frame, composed)
                if self.circular_portal_var.get():
                    portal_call_frame = self._apply_portal_circle_mask(portal_call_frame, engaged_any)

            # --- MOTOR DE JUEGOS KINECT AR (BOTÓN FLOTANTE, GESTO DE PELLIZCO Y SINCRONIZACIÓN P2P) ---
            if self.enable_kinect_games_hud_var.get():
                hands_c1 = s1.get("tracked_hands", [])
                hands_c2 = s2.get("tracked_hands", [])
                if self.cam2_blend > 0.5 and hands_c2:
                    active_hands = hands_c2
                elif hands_c1:
                    active_hands = hands_c1
                else:
                    active_hands = hands_c2 or hands_c1

                if in_p2p_call:
                    rem_game_pkt = self.p2p_node.get_remote_game_state()
                    if rem_game_pkt is not None:
                        self.kinect_engine.ingest_remote_sync_packet(rem_game_pkt)

                total_persons = max(1, s1.get("persons", 0) + s2.get("persons", 0))
                if portal_call_frame is not None:
                    portal_call_frame = self.kinect_engine.process_and_render(
                        portal_call_frame,
                        active_hands,
                        in_p2p_call=in_p2p_call,
                        persons_count=total_persons,
                    )
                    if composed is not None and rem_resized is None:
                        composed = portal_call_frame
                    elif composed is not None:
                        # Si estamos en llamada P2P, mostrar también la capa de juego en el monitor Admin
                        composed = portal_call_frame
                elif composed is not None:
                    composed = self.kinect_engine.process_and_render(
                        composed,
                        active_hands,
                        in_p2p_call=in_p2p_call,
                        persons_count=total_persons,
                    )

                if in_p2p_call:
                    self.p2p_node.send_game_state(self.kinect_engine.export_local_sync_packet())

            display_admin_frame = composed if composed is not None else portal_call_frame
            if display_admin_frame is None:
                time.sleep(0.02)
                continue

            self.processed_frame = display_admin_frame
            self.frame_count += 1
            now = time.time()
            if now - self.last_fps_time >= 1.0:
                self.actual_fps = self.frame_count / (now - self.last_fps_time)
                self.frame_count = 0
                self.last_fps_time = now

            if self.is_recording and self.video_writer is not None:
                try:
                    self.video_writer.write(display_admin_frame)
                except Exception:
                    pass

            combined_stats = {
                "persons": s1["persons"] + s2["persons"],
                "eye_contact": s1["eye_contact"] + s2["eye_contact"],
                "waving": s1["waving"] + s2["waving"],
                "offensive": s1.get("offensive", 0) + s2.get("offensive", 0),
                "evidences_saved": s1.get("evidences_saved", 0) + s2.get("evidences_saved", 0),
                "zoom": max(s1["zoom"], s2["zoom"]),
                "motion_pct": max(s1["motion_pct"], s2["motion_pct"]),
                "mode": s2["mode"] if (p2 is not None and s2.get("engaged")) else s1["mode"],
                "engaged": engaged_any,
                "profiles1": s1.get("profiles", []),
                "profiles2": s2.get("profiles", []) if (p2 is not None and self.enable_cam2_var.get()) else [],
            }

            # --- DISPARADOR PUNTUAL DE GEMINI API (SOLO EN EVENTOS ESPECÍFICOS PARA NO GASTAR TOKENS) ---
            if self.gemini_enabled_var.get():
                raw_active = raw2 if (raw2 is not None and self.cam2_blend > 0.5) else (raw1 if raw1 is not None else raw2)
                if raw_active is not None:
                    if combined_stats["offensive"] > 0 and self.gemini_trigger_offense_var.get():
                        def _on_gem_audit(res: dict):
                            # Si Gemini dictamina con certeza que fue un falso positivo, liberar el bloqueo local
                            if not res.get("is_offensive_confirmed", True) and float(res.get("confidence", 0.0)) >= 0.72:
                                self.ai_cam1.offensive_hold.clear()
                                self.ai_cam2.offensive_hold.clear()
                            self.root.after(0, lambda: self.gemini_usage_var.set(self.gemini_verifier.get_usage_summary()))

                        self.gemini_verifier.request_point_analysis_async(
                            raw_active,
                            trigger_reason="Auditoría Puntual de Acto / Moderación",
                            force_manual=False,
                            on_complete=_on_gem_audit,
                        )
                    elif engaged_any and self.gemini_trigger_1to1_var.get():
                        self.gemini_verifier.request_point_analysis_async(
                            raw_active,
                            trigger_reason="Perfilamiento Puntual en Conexión 1 a 1",
                            person_id=1,
                            force_manual=False,
                            on_complete=lambda _: self.root.after(
                                0, lambda: self.gemini_usage_var.set(self.gemini_verifier.get_usage_summary())
                            ),
                        )

            if not self.ui_busy:
                self.ui_busy = True
                self._render_to_ui(display_admin_frame, portal_call_frame, combined_stats)

            elapsed = time.time() - t_start
            if elapsed < 0.028:
                time.sleep(0.028 - elapsed)

    def _overlay_pip(self, base_img: np.ndarray, pip_img: np.ndarray, title: str = "") -> np.ndarray:
        bh, bw = base_img.shape[:2]
        pw, ph = int(bw * 0.25), int(bh * 0.25)
        pip_small = cv2.resize(pip_img, (pw, ph), interpolation=cv2.INTER_NEAREST)
        x0 = bw - pw - 20
        y0 = bh - ph - 20
        cv2.rectangle(base_img, (x0 - 2, y0 - 2), (x0 + pw + 2, y0 + ph + 2), (56, 189, 248), 2)
        base_img[y0 : y0 + ph, x0 : x0 + pw] = pip_small
        return base_img

    def _apply_portal_circle_mask(self, img: np.ndarray, engaged: bool) -> np.ndarray:
        h, w = img.shape[:2]
        out = np.full_like(img, 10)
        cx, cy = w // 2, h // 2
        radius = int(min(w, h) * 0.46)

        mask = np.zeros((h, w), dtype=np.uint8)
        cv2.circle(mask, (cx, cy), radius, 255, -1, cv2.LINE_AA)
        out[mask == 255] = img[mask == 255]

        ring_color = (74, 222, 128) if engaged else (248, 250, 252)
        cv2.circle(out, (cx, cy), radius, ring_color, 5, cv2.LINE_AA)
        cv2.circle(out, (cx, cy), radius + 9, (56, 189, 248), 2, cv2.LINE_AA)
        return out

    def _render_to_ui(self, frame: np.ndarray, portal_call_frame: np.ndarray, stats: dict):
        h, w = frame.shape[:2]
        lbl_w = max(self.video_label.winfo_width(), 640)
        lbl_h = max(self.video_label.winfo_height(), 480)

        scale = min(lbl_w / w, lbl_h / h)
        new_w = max(int(w * scale), 1)
        new_h = max(int(h * scale), 1)

        if abs(new_w - w) > 20 or abs(new_h - h) > 20:
            resized = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_NEAREST)
        else:
            resized = frame

        rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)

        if self.is_recording:
            cv2.circle(rgb, (28, 28), 10, (239, 68, 68), -1)
            cv2.putText(rgb, "REC", (46, 35), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (239, 68, 68), 2)

        pil_img = Image.fromarray(rgb)

        # Preparar imagen limpia para la ventana 'Portal Llamada 24/7' (Solo Imagen y Movimientos)
        portal_pil_img = None
        if (
            portal_call_frame is not None
            and self.portal_call_win is not None
            and self.portal_call_lbl is not None
        ):
            try:
                if self.portal_call_win.winfo_exists():
                    pw = max(self.portal_call_lbl.winfo_width(), 640)
                    ph = max(self.portal_call_lbl.winfo_height(), 480)
                    fh, fw = portal_call_frame.shape[:2]
                    pscale = min(pw / fw, ph / fh)
                    pnw = max(int(fw * pscale), 1)
                    pnh = max(int(fh * pscale), 1)
                    if abs(pnw - fw) > 20 or abs(pnh - fh) > 20:
                        presized = cv2.resize(portal_call_frame, (pnw, pnh), interpolation=cv2.INTER_NEAREST)
                    else:
                        presized = portal_call_frame
                    prgb = cv2.cvtColor(presized, cv2.COLOR_BGR2RGB)
                    portal_pil_img = Image.fromarray(prgb)
            except Exception:
                portal_pil_img = None

        def _update():
            try:
                if not self.running:
                    return
                tk_img = ImageTk.PhotoImage(image=pil_img)
                self.video_label.configure(image=tk_img, text="")
                self.video_label.image = tk_img

                if (
                    portal_pil_img is not None
                    and self.portal_call_win is not None
                    and self.portal_call_win.winfo_exists()
                    and self.portal_call_lbl is not None
                ):
                    ptk_img = ImageTk.PhotoImage(image=portal_pil_img)
                    self.portal_call_lbl.configure(image=ptk_img, text="")
                    self.portal_call_lbl.image = ptk_img

                self.fps_lbl.configure(text=f"⚡ FPS: {self.actual_fps:.1f} (Zero-Lag) | Señal: {w}x{h} | Zoom: {stats['zoom']:.2f}x")
                self.ai_banner_var.set(stats["mode"])
                if stats["offensive"] > 0:
                    self.ai_mode_lbl.configure(fg="#f87171")
                elif stats["engaged"]:
                    self.ai_mode_lbl.configure(fg="#4ade80")
                else:
                    self.ai_mode_lbl.configure(fg="#38bdf8")

                self.ai_telemetry_var.set(
                    f"Personas: {stats['persons']}  |  👁️ Mirada: {stats['eye_contact']}  |  "
                    f"👋 Saludos: {stats['waving']}  |  🚫 Actos Confirmados: {stats['offensive']}  |  "
                    f"📸 Evidencias: {stats.get('evidences_saved', 0)}"
                )
                self._update_profile_panels(
                    stats.get("profiles1", []),
                    stats.get("profiles2", []),
                    stats["mode"],
                    stats["motion_pct"],
                )
            finally:
                self.ui_busy = False

        self.root.after(0, _update)

    def trigger_manual_evidence_screenshot(self):
        """
        Toma un Super Screenshot de Evidencia instantáneo y lo guarda en evidencias_mal_uso
        sin bloquear la señal de video.
        """
        raw1, _, ok1 = self.grabber1.get_latest() if self.grabber1 else (None, 0, False)
        raw2, _, ok2 = self.grabber2.get_latest() if self.grabber2 else (None, 0, False)
        frame_src = raw2 if (ok2 and raw2 is not None) else (raw1 if (ok1 and raw1 is not None) else self.processed_frame)
        if frame_src is None:
            self.status_var.set("⚠️ Inicia la señal primero para capturar evidencia.")
            return

        ai_target = self.ai_cam2 if (ok2 and raw2 is not None) else self.ai_cam1
        with ai_target.state_lock:
            analyses = list(ai_target.last_analyses)
            hands = list(ai_target.last_hands)

        h_img, w_img = frame_src.shape[:2]
        pa = analyses[0] if analyses else PersonAnalysis(person_id=1, bbox=(int(w_img * 0.2), int(h_img * 0.1), int(w_img * 0.6), int(h_img * 0.8)), confidence=1.0)
        h_info = hands[0] if hands else HandGestureInfo(
            bbox=pa.bbox,
            landmarks=np.zeros((21, 3), dtype=np.float32),
            gesture_name="🚫 CAPTURA MANUAL DE EVIDENCIA (OPERADOR)",
            is_offensive=True,
            is_wave_pose=False,
            confidence=1.0,
            category="manual_operator",
        )
        ai_target.last_evidence_time[pa.person_id] = 0.0
        ai_target._save_evidence_screenshot_async(frame_src.copy(), pa, h_info)
        self.status_var.set(f"⚡ Super Screenshot de Evidencia guardado en: {self.evidences_dir}")

    def reset_full_lens(self):
        for ai in (self.ai_cam1, self.ai_cam2):
            ai.last_attention_time = 0.0
            ai.last_motion_time = time.time()
            ai.spring.set_target(0.5, 0.5, 1.0, deadzone_pos=0.0, deadzone_zoom=0.0)
        self.status_var.set("🌐 Vista restablecida suavemente al lente completo (1.0x)")

    def trigger_both_autofocus(self):
        def _worker():
            for ip, user, pwd in [
                (self.ip1_var.get().strip(), self.user1_var.get().strip(), self.pass1_var.get()),
                (self.ip2_var.get().strip(), self.user2_var.get().strip(), self.pass2_var.get()),
            ]:
                try:
                    requests.get(
                        f"http://{ip}/cgi-bin/devVideoInput.cgi?action=autoFocus",
                        auth=HTTPDigestAuth(user, pwd),
                        timeout=2.0,
                    )
                except Exception:
                    pass
            self.root.after(0, lambda: self.status_var.set("🎯 AutoFocus óptico enviado a las cámaras"))

        threading.Thread(target=_worker, daemon=True).start()

    def stop_unified_signal(self):
        self.running = False
        self.ui_busy = False
        if self.grabber1:
            self.grabber1.stop()
            self.grabber1 = None
        if self.grabber2:
            self.grabber2.stop()
            self.grabber2 = None
        if self.is_recording:
            self.toggle_recording()
        self.btn_connect.configure(state=tk.NORMAL)
        self.btn_disconnect.configure(state=tk.DISABLED)
        self.status_var.set("Señal detenida")

    def take_snapshot(self):
        if self.processed_frame is None:
            return
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = os.path.join(self.captures_dir, f"portal_dahua_{ts}.jpg")
        cv2.imwrite(filepath, self.processed_frame)
        self.status_var.set(f"📸 Captura guardada: {os.path.basename(filepath)}")

    def toggle_recording(self):
        if not self.is_recording:
            if self.processed_frame is None:
                return
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            self.record_filename = os.path.join(self.captures_dir, f"portal_dahua_{ts}.mp4")
            h, w = self.processed_frame.shape[:2]
            fourcc = cv2.VideoWriter_fourcc(*"mp4v")
            fps = self.actual_fps if self.actual_fps > 5 else 25.0
            self.video_writer = cv2.VideoWriter(self.record_filename, fourcc, fps, (w, h))
            self.is_recording = True
            self.btn_record.configure(text="⏹ Fin Grabación", bg="#dc2626")
            self.status_var.set(f"⏺ Grabando señal unificada en {os.path.basename(self.record_filename)}...")
        else:
            self.is_recording = False
            if self.video_writer:
                self.video_writer.release()
                self.video_writer = None
            self.btn_record.configure(text="⏺ Grabar", bg="#475569")
            self.status_var.set(f"✅ Video guardado: {os.path.basename(self.record_filename)}")

    def on_close(self):
        self._save_persistent_config()
        self.running = False
        if self.grabber1:
            self.grabber1.stop()
        if self.grabber2:
            self.grabber2.stop()
        if hasattr(self, "p2p_node") and self.p2p_node:
            self.p2p_node.stop()
        if self.is_recording and self.video_writer:
            self.video_writer.release()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    app = DahuaPortalViewerApp(root)
    root.mainloop()
