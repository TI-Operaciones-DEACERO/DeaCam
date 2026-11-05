#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DeaCam Portal Lite 24/7 — Videollamada P2P Mesh Grupal (Hasta 3 Participantes)
=============================================================================
Enfoque Principal:
- Pantalla Completa LIMPIA (Modo Espejo Puro sin estorbos, menús ni barras).
- Enlace P2P Mesh 24/7 Automático:
    * Si 2 usuarios lo ejecutan en la misma red -> Se miran mutuamente en modo 1 a 1 Fullscreen.
    * Si 3 usuarios lo ejecutan en la misma red -> Se enlazan automáticamente en llamada grupal 24/7 (hasta 3 participantes).
- Video Inteligente & Cinemático:
    * Zoom dinámico suave enfocado en la persona cuando hay contacto visual (1.0x a 1.65x).
    * Oscilación suave relajante si no hay nadie frente a la cámara.
    * Movimientos ultra lentos y agradables (Omega 0.45, sub-píxel continuo).
- Audio Full-Duplex:
    * Micrófono y Parlante (Altavoz) con mezcla continua de todos los participantes.
- Ventana Independiente de Configuración:
    * Se abre pulsando F12, Esc, Ctrl+A o Clic Derecho (o mediante el botón flotante discreto en esquina).
    * Permite activar/desactivar micrófono, cambiar cámara, ajustar volumen y sensibilidades.
"""

import os
import sys
import time
import json
import threading
import tkinter as tk
from tkinter import ttk, messagebox

import cv2
import numpy as np
from PIL import Image, ImageTk

from p2p_portal import (
    UniversalCameraGrabber,
    AudioStreamEngine,
    discover_usb_cameras,
    get_local_lan_ips,
)
from p2p_mesh_lite import MultiPeerMeshNode
from lite_pipeline import LiteVideoPipeline, PanoramicSweeper


class PortalLiteApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("DeaCam Portal Lite 24/7")
        self.root.configure(bg="#000000")

        base_dir = os.path.dirname(os.path.abspath(__file__))
        self.config_path = os.path.join(base_dir, "config_portal.json")
        self.models_dir = os.path.join(base_dir, "models")

        # Estado de pantalla completa sin bordes
        self.fullscreen = ("--windowed" not in sys.argv and "--window" not in sys.argv)
        self.cursor_timer = None
        self.config_win = None

        # Dispositivos de Audio
        self.audio_inputs_list, self.audio_outputs_list = AudioStreamEngine.list_audio_devices()
        self.usb_cameras_list = [{"index": 0, "label": "USB 0: Cámara Local / Webcam Predeterminada"}]

        # --- Variables de Configuración Persistente ---
        self.enable_mic_var = tk.BooleanVar(value=True)
        self.enable_speaker_var = tk.BooleanVar(value=True)
        self.speaker_volume_var = tk.DoubleVar(value=1.0)
        self.mic_gain_var = tk.DoubleVar(value=1.0)
        self.mic_device_var = tk.StringVar(value=self.audio_inputs_list[0]["label"])
        self.speaker_device_var = tk.StringVar(value=self.audio_outputs_list[0]["label"])

        self.camera_source_mode_var = tk.StringVar(value="Dahua Dual / Simple PoE (RTSP Automático)")
        self.usb_cam_var = tk.StringVar(value="USB 0: Cámara Local / Webcam Predeterminada")
        self.custom_rtsp_var = tk.StringVar(value="rtsp://admin:SomosUnaFamilia2025.@192.168.1.109:554/cam/realmonitor?channel=1&subtype=0")

        # Variables de Cámara Secundaria (Panorámica con Barrido Izq <-> Der)
        self.enable_cam2_var = tk.BooleanVar(value=True)
        self.auto_swap_cam2_var = tk.BooleanVar(value=True)
        self.cam2_source_mode_var = tk.StringVar(value="Dahua PoE Cámara 2 (RTSP Panorámica)")
        self.custom_rtsp2_var = tk.StringVar(value="rtsp://admin:SomosUnaFamilia2025.@192.168.1.108:554/cam/realmonitor?channel=1&subtype=0")
        self.usb_cam2_var = tk.StringVar(value="USB 1: Segunda Webcam")
        self.cam2_sweep_speed_var = tk.DoubleVar(value=0.05)

        self.mirror_mode_var = tk.BooleanVar(value=True)
        self.dynamic_zoom_var = tk.BooleanVar(value=True)
        self.idle_patrol_var = tk.BooleanVar(value=True)
        self.smooth_omega_var = tk.DoubleVar(value=0.45)
        self.max_zoom_var = tk.DoubleVar(value=1.65)
        self.pip_self_var = tk.BooleanVar(value=False)

        self.status_banner_var = tk.StringVar(value="Iniciando Portal Lite 24/7...")
        self.target_peer_ip_var = tk.StringVar(value="")
        self.target_peer_ip2_var = tk.StringVar(value="")

        self._load_config()

        # Componentes Core
        self.mesh_node = MultiPeerMeshNode()
        target_ips = [
            ip for ip in [self.target_peer_ip_var.get().strip(), self.target_peer_ip2_var.get().strip()]
            if ip and not ip.startswith("Auto")
        ]
        if target_ips:
            self.mesh_node.set_target_peers(target_ips)
        self.pipeline = LiteVideoPipeline(self.models_dir)
        self.sweeper = PanoramicSweeper(speed=float(self.cam2_sweep_speed_var.get()))
        self.grabber1: Optional[UniversalCameraGrabber] = None
        self.grabber2: Optional[UniversalCameraGrabber] = None
        self.last_interaction_ts = time.time()

        # Sincronizar audio con el nodo mesh
        self._sync_audio_settings()

        # Construir Interfaz de Video Fullscreen Limpia
        self._build_fullscreen_ui()

        # Iniciar captura y red mesh
        self.running = True
        self.start_camera_grabber()
        self.mesh_node.start()

        # Bucle compositor de video (60 FPS fluidos)
        threading.Thread(target=self._render_loop, daemon=True).start()
        threading.Thread(target=self._status_updater_loop, daemon=True).start()

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _sync_audio_settings(self):
        ae = self.mesh_node.audio_engine
        ae.mic_muted = not bool(self.enable_mic_var.get())
        ae.speaker_muted = not bool(self.enable_speaker_var.get())
        ae.speaker_volume = float(self.speaker_volume_var.get())
        ae.mic_gain = float(self.mic_gain_var.get())

        in_idx = self._resolve_audio_idx(self.mic_device_var.get(), self.audio_inputs_list)
        out_idx = self._resolve_audio_idx(self.speaker_device_var.get(), self.audio_outputs_list)
        ae.configure_devices(in_idx, out_idx)

    def _resolve_audio_idx(self, label_str: str, dev_list: list):
        for d in dev_list:
            if d["label"] == label_str:
                return d["index"]
        if label_str.startswith("[") and "]" in label_str:
            try:
                return int(label_str.split("]", 1)[0].strip("["))
            except Exception:
                pass
        return None

    def _load_config(self):
        if not os.path.exists(self.config_path):
            return
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)

            if "enable_mic" in cfg:
                self.enable_mic_var.set(bool(cfg["enable_mic"]))
            if "enable_speaker" in cfg:
                self.enable_speaker_var.set(bool(cfg["enable_speaker"]))
            if "speaker_volume" in cfg:
                self.speaker_volume_var.set(float(cfg["speaker_volume"]))
            if "mic_gain" in cfg:
                self.mic_gain_var.set(float(cfg["mic_gain"]))
            if "mic_device" in cfg:
                self.mic_device_var.set(str(cfg["mic_device"]))
            if "speaker_device" in cfg:
                self.speaker_device_var.set(str(cfg["speaker_device"]))
            if "camera_source_mode" in cfg:
                self.camera_source_mode_var.set(str(cfg["camera_source_mode"]))
            if "usb_cam1" in cfg:
                self.usb_cam_var.set(str(cfg["usb_cam1"]))
            if "custom_rtsp1" in cfg:
                self.custom_rtsp_var.set(str(cfg["custom_rtsp1"]))
            if "smooth_omega" in cfg:
                self.smooth_omega_var.set(float(cfg["smooth_omega"]))
            if "max_zoom" in cfg:
                self.max_zoom_var.set(float(cfg["max_zoom"]))
            if "mirror_mode" in cfg:
                self.mirror_mode_var.set(bool(cfg["mirror_mode"]))
            if "enable_cam2" in cfg:
                self.enable_cam2_var.set(bool(cfg["enable_cam2"]))
            if "auto_swap_cam2" in cfg:
                self.auto_swap_cam2_var.set(bool(cfg["auto_swap_cam2"]))
            if "cam2_source_mode" in cfg:
                self.cam2_source_mode_var.set(str(cfg["cam2_source_mode"]))
            if "custom_rtsp2" in cfg:
                self.custom_rtsp2_var.set(str(cfg["custom_rtsp2"]))
            if "usb_cam2" in cfg:
                self.usb_cam2_var.set(str(cfg["usb_cam2"]))
            if "cam2_sweep_speed" in cfg:
                spd = float(cfg["cam2_sweep_speed"])
                self.cam2_sweep_speed_var.set(spd)
                if hasattr(self, "sweeper"):
                    self.sweeper.speed = spd
            if "target_peer_ip" in cfg:
                tip = str(cfg["target_peer_ip"]).strip()
                if not tip.startswith("Auto"):
                    self.target_peer_ip_var.set(tip)
            if "target_peer_ip2" in cfg:
                tip2 = str(cfg["target_peer_ip2"]).strip()
                if not tip2.startswith("Auto"):
                    self.target_peer_ip2_var.set(tip2)
        except Exception:
            pass

    def _sync_peers_to_node(self):
        targets = []
        p1 = self.target_peer_ip_var.get().strip()
        p2 = self.target_peer_ip2_var.get().strip()
        if p1 and not p1.startswith("Auto"):
            targets.append(p1)
        if p2 and not p2.startswith("Auto"):
            targets.append(p2)
        self.mesh_node.set_target_peers(targets)
        self.save_config()

    def save_config(self):
        try:
            cfg = {}
            if os.path.exists(self.config_path):
                try:
                    with open(self.config_path, "r", encoding="utf-8") as f:
                        cfg = json.load(f)
                except Exception:
                    cfg = {}

            cfg.update({
                "enable_mic": bool(self.enable_mic_var.get()),
                "enable_speaker": bool(self.enable_speaker_var.get()),
                "speaker_volume": float(self.speaker_volume_var.get()),
                "mic_gain": float(self.mic_gain_var.get()),
                "mic_device": self.mic_device_var.get(),
                "speaker_device": self.speaker_device_var.get(),
                "camera_source_mode": self.camera_source_mode_var.get(),
                "usb_cam1": self.usb_cam_var.get(),
                "custom_rtsp1": self.custom_rtsp_var.get(),
                "enable_cam2": bool(self.enable_cam2_var.get()),
                "auto_swap_cam2": bool(self.auto_swap_cam2_var.get()),
                "cam2_source_mode": self.cam2_source_mode_var.get(),
                "custom_rtsp2": self.custom_rtsp2_var.get(),
                "usb_cam2": self.usb_cam2_var.get(),
                "cam2_sweep_speed": float(self.cam2_sweep_speed_var.get()),
                "smooth_omega": float(self.smooth_omega_var.get()),
                "max_zoom": float(self.max_zoom_var.get()),
                "mirror_mode": bool(self.mirror_mode_var.get()),
                "target_peer_ip": self.target_peer_ip_var.get().strip() or "Auto-Descubrimiento LAN P2P (24/7)",
                "target_peer_ip2": self.target_peer_ip2_var.get().strip(),
            })
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    def start_camera_grabber(self):
        # 1. Cámara 1 (Principal 1 a 1)
        if self.grabber1 is not None:
            self.grabber1.stop()
            self.grabber1 = None

        cam_mode = self.camera_source_mode_var.get()
        mirror_func = lambda: self.mirror_mode_var.get()

        if cam_mode.startswith("Cámara USB"):
            usb_lbl = self.usb_cam_var.get()
            usb_idx = "0"
            for ch in usb_lbl.split():
                if ch.isdigit():
                    usb_idx = ch
                    break
            self.grabber1 = UniversalCameraGrabber(
                f"USB:{usb_idx}",
                mirror_fn=mirror_func,
                source_type="usb",
            )
        elif cam_mode.startswith("Cámara IP Universal"):
            self.grabber1 = UniversalCameraGrabber(
                self.custom_rtsp_var.get().strip(),
                mirror_fn=mirror_func,
                source_type="rtsp",
            )
        else:
            # Dahua PoE 1 Predeterminada (Subflujo HD Zero-Lag)
            rtsp_url = self.custom_rtsp_var.get().strip()
            self.grabber1 = UniversalCameraGrabber(
                rtsp_url,
                mirror_fn=mirror_func,
                source_type="rtsp",
            )

        # 2. Cámara 2 (Secundaria Panorámica con Barrido)
        if self.grabber2 is not None:
            self.grabber2.stop()
            self.grabber2 = None

        if self.enable_cam2_var.get():
            cam2_mode = self.cam2_source_mode_var.get()
            if cam2_mode.startswith("Cámara USB"):
                usb2_lbl = self.usb_cam2_var.get()
                usb2_idx = "1"
                for ch in usb2_lbl.split():
                    if ch.isdigit():
                        usb2_idx = ch
                        break
                self.grabber2 = UniversalCameraGrabber(
                    f"USB:{usb2_idx}",
                    mirror_fn=lambda: False,
                    source_type="usb",
                )
            elif cam2_mode.startswith("Cámara IP"):
                self.grabber2 = UniversalCameraGrabber(
                    self.custom_rtsp2_var.get().strip(),
                    mirror_fn=lambda: False,
                    source_type="rtsp",
                )
            else:
                # Dahua PoE Cámara 2 (192.168.1.108 Panorámica)
                rtsp2_url = self.custom_rtsp2_var.get().strip()
                self.grabber2 = UniversalCameraGrabber(
                    rtsp2_url,
                    mirror_fn=lambda: False,
                    source_type="rtsp",
                )

        if hasattr(self, "sweeper"):
            self.sweeper.speed = float(self.cam2_sweep_speed_var.get())

    def _build_fullscreen_ui(self):
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        if self.fullscreen:
            self.root.geometry(f"{sw}x{sh}+0+0")
            try:
                self.root.attributes("-fullscreen", True)
            except Exception:
                try:
                    self.root.overrideredirect(True)
                except Exception:
                    pass
        else:
            self.root.geometry("1280x720+60+40")
            self.root.attributes("-fullscreen", False)

        self.canvas_label = tk.Label(
            self.root,
            bg="#000000",
            bd=0,
            highlightthickness=0,
            text="",
        )
        self.canvas_label.pack(fill=tk.BOTH, expand=True)

        # Botón discreto en la esquina superior derecha para configurar sin teclado
        self.gear_btn = tk.Button(
            self.root,
            text="⚙️",
            command=self.open_config_window,
            bg="#111827",
            fg="#94a3b8",
            activebackground="#1e293b",
            activeforeground="#38bdf8",
            relief=tk.FLAT,
            bd=0,
            font=("Segoe UI", 11),
            padx=6,
            pady=2,
            cursor="hand2",
        )
        self.gear_btn.place(relx=0.985, rely=0.015, anchor="ne")

        # Indicador de estado discreto en la esquina inferior izquierda
        self.badge_lbl = tk.Label(
            self.root,
            textvariable=self.status_banner_var,
            bg="#030712",
            fg="#38bdf8",
            font=("Segoe UI", 8, "bold"),
            padx=10,
            pady=3,
            relief=tk.FLAT,
            highlightbackground="#1e293b",
            highlightthickness=1,
        )
        self.badge_lbl.place(relx=0.015, rely=0.975, anchor="sw")

        # Enlace de eventos de mouse y teclado
        self.root.bind("<Motion>", self._on_activity)
        self.canvas_label.bind("<Motion>", self._on_activity)
        self.root.bind("<Button-3>", lambda e: self.open_config_window())
        self.canvas_label.bind("<Button-3>", lambda e: self.open_config_window())

        self.root.bind("<F12>", lambda e: self.open_config_window())
        self.root.bind("<Escape>", lambda e: self.open_config_window())
        self.root.bind("<Control-a>", lambda e: self.open_config_window())
        self.root.bind("<Control-A>", lambda e: self.open_config_window())
        self.root.bind("<F11>", lambda e: self._toggle_fullscreen())
        self.canvas_label.bind("<Double-Button-1>", lambda e: self._toggle_fullscreen())

        self._on_activity()

    def _toggle_fullscreen(self):
        self.fullscreen = not self.fullscreen
        try:
            if self.fullscreen:
                sw = self.root.winfo_screenwidth()
                sh = self.root.winfo_screenheight()
                self.root.geometry(f"{sw}x{sh}+0+0")
                self.root.attributes("-fullscreen", True)
            else:
                self.root.attributes("-fullscreen", False)
                self.root.geometry("1280x720+60+40")
                self.root.config(cursor="arrow")
        except Exception:
            try:
                if self.fullscreen:
                    self.root.overrideredirect(True)
                else:
                    self.root.overrideredirect(False)
            except Exception:
                pass

    def _on_activity(self, event=None):
        self.last_interaction_ts = time.time()
        try:
            self.root.config(cursor="arrow")
            self.gear_btn.place(relx=0.985, rely=0.015, anchor="ne")
        except Exception:
            pass

        if self.cursor_timer is not None:
            try:
                self.root.after_cancel(self.cursor_timer)
            except Exception:
                pass
        self.cursor_timer = self.root.after(3000, self._hide_cursor)

    def _hide_cursor(self):
        if self.fullscreen and self.root.winfo_exists():
            try:
                self.root.config(cursor="none")
                # Ocultar ligeramente el botón si nadie mueve el mouse
                self.gear_btn.place_forget()
            except Exception:
                pass

    def _status_updater_loop(self):
        while self.running:
            try:
                count, msg = self.mesh_node.get_status_summary()
                mic_state = "🎤 ON" if self.enable_mic_var.get() else "🔇 MUTE"
                spk_state = "🔊 ON" if self.enable_speaker_var.get() else "🔈 OFF"
                c2_state = "📹 Cam2 Pan ON" if self.enable_cam2_var.get() else "📹 Cam2 OFF"
                self.status_banner_var.set(f"{msg}  |  {mic_state}  |  {spk_state}  |  {c2_state}")
            except Exception:
                pass
            time.sleep(1.2)

    def _render_loop(self):
        cw, ch = 1280, 720
        while self.running:
            t0 = time.time()

            # 1. Cuadro de Cámara 1 (Principal 1 a 1)
            raw_local, _, ok_local = self.grabber1.get_latest() if self.grabber1 else (None, 0, False)
            remote_streams = self.mesh_node.get_remote_streams()

            local_processed = None
            if ok_local and raw_local is not None:
                local_processed, stats = self.pipeline.process_frame(
                    raw_local,
                    enable_dynamic_zoom=self.dynamic_zoom_var.get(),
                    enable_idle_patrol=self.idle_patrol_var.get(),
                    max_zoom=float(self.max_zoom_var.get()),
                    smooth_omega=float(self.smooth_omega_var.get()),
                    target_size=(cw, ch),
                )
                if stats.get("faces", 0) > 0:
                    self.last_interaction_ts = time.time()
                # Difundir a la red P2P Mesh 24/7
                self.mesh_node.send_local_video_frame(local_processed)

            # 2. Cuadro de Cámara 2 (Secundaria Panorámica con Barrido)
            cam2_swept = None
            if self.enable_cam2_var.get() and self.grabber2:
                raw2, _, ok2 = self.grabber2.get_latest()
                if ok2 and raw2 is not None:
                    cam2_swept = self.sweeper.process_sweep(raw2, target_size=(cw, ch))

            # 3. Composición de la señal de llamada (1 a 1, Espejo o Grupal de 3)
            if not remote_streams:
                if local_processed is not None:
                    call_canvas = local_processed
                else:
                    call_canvas = np.full((ch, cw, 3), 12, dtype=np.uint8)
                    cv2.putText(
                        call_canvas,
                        "ESPERANDO ENLACE DE LLAMADA 24/7 EN RED LOCAL...",
                        (int(cw * 0.18), int(ch * 0.5)),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.75,
                        (56, 189, 248),
                        2,
                        cv2.LINE_AA,
                    )
            elif len(remote_streams) == 1:
                r_frame = remote_streams[0]["frame"]
                if r_frame.shape[1] != cw or r_frame.shape[0] != ch:
                    r_frame = cv2.resize(r_frame, (cw, ch), interpolation=cv2.INTER_LINEAR)
                call_canvas = r_frame.copy()
            else:
                call_canvas = np.full((ch, cw, 3), 10, dtype=np.uint8)
                half_w = cw // 2
                half_h = ch

                r1 = remote_streams[0]["frame"]
                r1_res = cv2.resize(r1, (half_w, half_h), interpolation=cv2.INTER_LINEAR)
                call_canvas[:, :half_w] = r1_res

                r2 = remote_streams[1]["frame"]
                r2_res = cv2.resize(r2, (half_w, half_h), interpolation=cv2.INTER_LINEAR)
                call_canvas[:, half_w:] = r2_res
                cv2.line(call_canvas, (half_w, 0), (half_w, ch), (56, 189, 248), 2)

            # 4. Dinámica de Inactividad vs Interacción (Primer Plano vs PiP pequeño)
            # ¿Nadie interactúa por > 6 segundos?
            is_idle = (time.time() - self.last_interaction_ts > 6.0)

            if cam2_swept is not None and self.enable_cam2_var.get():
                if is_idle and self.auto_swap_cam2_var.get():
                    # MODO INACTIVO: Cámara Panorámica en PRIMER PLANO (Pantalla Completa)
                    final_canvas = cam2_swept.copy()
                    # Vista de la llamada en PiP pequeño en esquina
                    pw, ph = int(cw * 0.24), int(ch * 0.24)
                    call_small = cv2.resize(call_canvas, (pw, ph), interpolation=cv2.INTER_LINEAR)
                    x0 = cw - pw - 24
                    y0 = ch - ph - 24
                    cv2.rectangle(final_canvas, (x0 - 2, y0 - 2), (x0 + pw + 2, y0 + ph + 2), (56, 189, 248), 2)
                    final_canvas[y0 : y0 + ph, x0 : x0 + pw] = call_small
                    cv2.rectangle(final_canvas, (x0, y0), (x0 + 85, y0 + 18), (15, 23, 42), -1)
                    cv2.putText(final_canvas, "LLAMADA", (x0 + 6, y0 + 13), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (56, 189, 248), 1, cv2.LINE_AA)
                else:
                    # MODO ACTIVO: Llamada 1 a 1 en PRIMER PLANO (Pantalla Completa)
                    final_canvas = call_canvas.copy()
                    # Cámara Panorámica en PiP pequeño en esquina con barrido
                    pw, ph = int(cw * 0.24), int(ch * 0.24)
                    pan_small = cv2.resize(cam2_swept, (pw, ph), interpolation=cv2.INTER_LINEAR)
                    x0 = cw - pw - 24
                    y0 = ch - ph - 24
                    cv2.rectangle(final_canvas, (x0 - 2, y0 - 2), (x0 + pw + 2, y0 + ph + 2), (56, 189, 248), 2)
                    final_canvas[y0 : y0 + ph, x0 : x0 + pw] = pan_small
                    cv2.rectangle(final_canvas, (x0, y0), (x0 + 110, y0 + 18), (15, 23, 42), -1)
                    cv2.putText(final_canvas, "PANORÁMICA", (x0 + 6, y0 + 13), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (56, 189, 248), 1, cv2.LINE_AA)
            else:
                final_canvas = call_canvas
                # PiP de mi propia cámara si el usuario lo activó
                if self.pip_self_var.get() and local_processed is not None:
                    pw, ph = int(cw * 0.22), int(ch * 0.22)
                    p_small = cv2.resize(local_processed, (pw, ph), interpolation=cv2.INTER_LINEAR)
                    x0 = cw - pw - 24
                    y0 = ch - ph - 24
                    cv2.rectangle(final_canvas, (x0 - 2, y0 - 2), (x0 + pw + 2, y0 + ph + 2), (56, 189, 248), 2)
                    final_canvas[y0 : y0 + ph, x0 : x0 + pw] = p_small

            # Renderizar a Tkinter
            self._display_to_screen(final_canvas)

            elapsed = time.time() - t0
            if elapsed < 0.024:
                time.sleep(0.024 - elapsed)

    def _display_to_screen(self, frame_bgr: np.ndarray):
        try:
            lbl_w = max(self.canvas_label.winfo_width(), 640)
            lbl_h = max(self.canvas_label.winfo_height(), 480)
            fh, fw = frame_bgr.shape[:2]

            scale = min(lbl_w / fw, lbl_h / fh)
            nw = max(1, int(fw * scale))
            nh = max(1, int(fh * scale))

            if abs(nw - fw) > 20 or abs(nh - fh) > 20:
                resized = cv2.resize(frame_bgr, (nw, nh), interpolation=cv2.INTER_NEAREST)
            else:
                resized = frame_bgr

            rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
            pil_img = Image.fromarray(rgb)
            tk_img = ImageTk.PhotoImage(image=pil_img)

            def _update():
                if not self.running:
                    return
                self.canvas_label.configure(image=tk_img, text="")
                self.canvas_label.image = tk_img

            self.root.after(0, _update)
        except Exception:
            pass

    # =========================================================================
    # VENTANA APARTE DE CONFIGURACIÓN (MICRÓFONO, PARLANTE, CÁMARAS Y ENFOQUE)
    # =========================================================================
    def open_config_window(self):
        """Abre la ventana flotante de ajustes sin interrumpir la videollamada 24/7."""
        if self.config_win is not None and self.config_win.winfo_exists():
            self.config_win.lift()
            return

        win = tk.Toplevel(self.root)
        win.title("Ajustes de Videollamada 24/7 (Audio, Cámara y Enfoque)")
        win.geometry("580x790")
        win.configure(bg="#0b0f19", padx=16, pady=14)
        win.transient(self.root)

        theme_card = "#111827"
        theme_border = "#1f2937"

        def _make_card(title: str, accent: str = "#38bdf8"):
            outer = tk.Frame(win, bg=theme_card, highlightbackground=theme_border, highlightthickness=1)
            outer.pack(fill=tk.X, pady=(0, 10))
            bar = tk.Frame(outer, bg=accent, height=2)
            bar.pack(fill=tk.X, side=tk.TOP)
            hdr = tk.Frame(outer, bg=theme_card, padx=10, pady=5)
            hdr.pack(fill=tk.X)
            tk.Label(hdr, text=title, bg=theme_card, fg="#f8fafc", font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT)
            body = tk.Frame(outer, bg=theme_card, padx=10, pady=8)
            body.pack(fill=tk.X)
            return body

        # --- TARJETA 1: ENLACE P2P Y CONEXIÓN DIRECTA POR IP ---
        net_card = _make_card("🌐 1. Conexión de Red P2P (Llamada 1 a 1 y Grupal hasta 3)", accent="#38bdf8")

        my_ips = [ip for ip in self.mesh_node.local_ips if not ip.startswith("127.")]
        my_ips_str = ", ".join(my_ips) if my_ips else "Detectando..."
        my_ip_row = tk.Frame(net_card, bg=theme_card)
        my_ip_row.pack(fill=tk.X, pady=(0, 4))
        tk.Label(my_ip_row, text="Mi IP Local (Dile esta IP a tus compañeros):", bg=theme_card, fg="#94a3b8", font=("Segoe UI", 8)).pack(side=tk.LEFT)
        tk.Label(my_ip_row, text=my_ips_str, bg=theme_card, fg="#38bdf8", font=("Segoe UI", 9, "bold")).pack(side=tk.RIGHT)

        # Integrante 2
        tip1_row = tk.Frame(net_card, bg=theme_card)
        tip1_row.pack(fill=tk.X, pady=2)
        tk.Label(tip1_row, text="IP Integrante 2:", bg=theme_card, fg="#f8fafc", font=("Segoe UI", 8, "bold"), width=15, anchor="w").pack(side=tk.LEFT)
        tk.Entry(
            tip1_row,
            textvariable=self.target_peer_ip_var,
            bg="#0b0f19",
            fg="#f8fafc",
            relief=tk.FLAT,
            highlightbackground=theme_border,
            highlightcolor="#38bdf8",
            highlightthickness=1,
            font=("Segoe UI", 9),
            width=18,
        ).pack(side=tk.LEFT, padx=4)

        def _connect_p1():
            t1 = self.target_peer_ip_var.get().strip()
            if t1:
                self._sync_peers_to_node()
                threading.Thread(target=lambda: self.mesh_node.connect_to_peer(t1), daemon=True).start()

        tk.Button(
            tip1_row,
            text="🔗 Conectar 2",
            command=_connect_p1,
            bg="#0284c7",
            fg="#ffffff",
            relief=tk.FLAT,
            font=("Segoe UI", 8, "bold"),
            padx=6,
            pady=1,
            cursor="hand2",
        ).pack(side=tk.RIGHT)

        # Integrante 3 (Tercer Integrante)
        tip2_row = tk.Frame(net_card, bg=theme_card)
        tip2_row.pack(fill=tk.X, pady=2)
        tk.Label(tip2_row, text="IP Integrante 3:", bg=theme_card, fg="#34d399", font=("Segoe UI", 8, "bold"), width=15, anchor="w").pack(side=tk.LEFT)
        tk.Entry(
            tip2_row,
            textvariable=self.target_peer_ip2_var,
            bg="#0b0f19",
            fg="#f8fafc",
            relief=tk.FLAT,
            highlightbackground=theme_border,
            highlightcolor="#38bdf8",
            highlightthickness=1,
            font=("Segoe UI", 9),
            width=18,
        ).pack(side=tk.LEFT, padx=4)

        def _connect_p2():
            t2 = self.target_peer_ip2_var.get().strip()
            if t2:
                self._sync_peers_to_node()
                threading.Thread(target=lambda: self.mesh_node.connect_to_peer(t2), daemon=True).start()

        tk.Button(
            tip2_row,
            text="🔗 Conectar 3",
            command=_connect_p2,
            bg="#059669",
            fg="#ffffff",
            relief=tk.FLAT,
            font=("Segoe UI", 8, "bold"),
            padx=6,
            pady=1,
            cursor="hand2",
        ).pack(side=tk.RIGHT)

        # Botón Enlazar Grupo (Todos)
        all_row = tk.Frame(net_card, bg=theme_card)
        all_row.pack(fill=tk.X, pady=(4, 0))

        def _connect_all():
            self._sync_peers_to_node()
            t1 = self.target_peer_ip_var.get().strip()
            t2 = self.target_peer_ip2_var.get().strip()
            if t1:
                threading.Thread(target=lambda: self.mesh_node.connect_to_peer(t1), daemon=True).start()
            if t2:
                threading.Thread(target=lambda: self.mesh_node.connect_to_peer(t2), daemon=True).start()

        tk.Button(
            all_row,
            text="👥 Enlazar a Ambos Integrantes (Llamada Grupal de 3)",
            command=_connect_all,
            bg="#4338ca",
            fg="#ffffff",
            relief=tk.FLAT,
            font=("Segoe UI", 8, "bold"),
            pady=3,
            cursor="hand2",
        ).pack(fill=tk.X)

        # --- TARJETA 2: CONEXIÓN DE AUDIO (MICRÓFONO Y PARLANTE) ---
        aud_card = _make_card("🔊 2. Micrófono y Parlante (Altavoz)", accent="#10b981")

        mic_row = tk.Frame(aud_card, bg=theme_card)
        mic_row.pack(fill=tk.X, pady=2)
        tk.Checkbutton(
            mic_row,
            text="🎤 Micrófono Activo:",
            variable=self.enable_mic_var,
            bg=theme_card,
            fg="#34d399",
            selectcolor="#0b0f19",
            activebackground=theme_card,
            font=("Segoe UI", 8, "bold"),
            command=self._sync_audio_settings,
        ).pack(side=tk.LEFT)
        mic_cb = ttk.Combobox(
            mic_row,
            textvariable=self.mic_device_var,
            values=[d["label"] for d in self.audio_inputs_list],
            state="readonly",
            width=36,
        )
        mic_cb.pack(side=tk.RIGHT)
        mic_cb.bind("<<ComboboxSelected>>", lambda e: self._sync_audio_settings())

        spk_row = tk.Frame(aud_card, bg=theme_card)
        spk_row.pack(fill=tk.X, pady=2)
        tk.Checkbutton(
            spk_row,
            text="🔊 Parlante Activo:",
            variable=self.enable_speaker_var,
            bg=theme_card,
            fg="#38bdf8",
            selectcolor="#0b0f19",
            activebackground=theme_card,
            font=("Segoe UI", 8, "bold"),
            command=self._sync_audio_settings,
        ).pack(side=tk.LEFT)
        spk_cb = ttk.Combobox(
            spk_row,
            textvariable=self.speaker_device_var,
            values=[d["label"] for d in self.audio_outputs_list],
            state="readonly",
            width=36,
        )
        spk_cb.pack(side=tk.RIGHT)
        spk_cb.bind("<<ComboboxSelected>>", lambda e: self._sync_audio_settings())

        vol_row = tk.Frame(aud_card, bg=theme_card)
        vol_row.pack(fill=tk.X, pady=(4, 0))
        tk.Label(vol_row, text="Volumen:", bg=theme_card, fg="#94a3b8", font=("Segoe UI", 8)).pack(side=tk.LEFT)
        tk.Scale(
            vol_row,
            from_=0.0,
            to=2.0,
            resolution=0.05,
            orient=tk.HORIZONTAL,
            variable=self.speaker_volume_var,
            bg=theme_card,
            fg="#38bdf8",
            highlightthickness=0,
            bd=0,
            troughcolor="#0b0f19",
            length=150,
            command=lambda v: self._sync_audio_settings(),
        ).pack(side=tk.LEFT, padx=6)

        tk.Button(
            vol_row,
            text="🔔 Probar Parlante",
            command=lambda: self.mesh_node.audio_engine.play_test_tone(),
            bg="#1f2937",
            fg="#f8fafc",
            relief=tk.FLAT,
            font=("Segoe UI", 8),
            cursor="hand2",
        ).pack(side=tk.RIGHT)

        # --- TARJETA 3: CONFIGURACIÓN DE CÁMARAS (PRINCIPAL Y SECUNDARIA PANORÁMICA) ---
        cam_card = _make_card("📷 3. Selección de Cámaras (Principal y Secundaria Panorámica)", accent="#38bdf8")

        # Cámara 1 (Principal 1 a 1)
        tk.Label(cam_card, text="Cámara 1 (Principal 1 a 1):", bg=theme_card, fg="#38bdf8", font=("Segoe UI", 8, "bold")).pack(anchor="w")
        cam_mode_cb = ttk.Combobox(
            cam_card,
            textvariable=self.camera_source_mode_var,
            values=[
                "Dahua Dual / Simple PoE (RTSP Automático)",
                "Cámara USB / Webcam Local (Universal)",
                "Cámara IP Universal (URL RTSP / HTTP / ONVIF)",
            ],
            state="readonly",
        )
        cam_mode_cb.pack(fill=tk.X, pady=(1, 3))
        tk.Entry(
            cam_card,
            textvariable=self.custom_rtsp_var,
            bg="#0b0f19",
            fg="#f8fafc",
            relief=tk.FLAT,
            highlightbackground=theme_border,
            highlightcolor="#38bdf8",
            highlightthickness=1,
            font=("Segoe UI", 8),
        ).pack(fill=tk.X, pady=(0, 5))

        # Cámara 2 (Secundaria Panorámica con Barrido)
        tk.Checkbutton(
            cam_card,
            text="📹 Activar Cámara 2 (Secundaria con Barrido Panorámico):",
            variable=self.enable_cam2_var,
            bg=theme_card,
            fg="#34d399",
            selectcolor="#0b0f19",
            activebackground=theme_card,
            font=("Segoe UI", 8, "bold"),
            anchor="w",
        ).pack(fill=tk.X, pady=(2, 1))

        cam2_mode_cb = ttk.Combobox(
            cam_card,
            textvariable=self.cam2_source_mode_var,
            values=[
                "Dahua PoE Cámara 2 (RTSP Panorámica)",
                "Cámara USB Secundaria (Webcam 2)",
                "Cámara IP Universal (URL RTSP / HTTP / ONVIF)",
            ],
            state="readonly",
        )
        cam2_mode_cb.pack(fill=tk.X, pady=(1, 3))
        tk.Entry(
            cam_card,
            textvariable=self.custom_rtsp2_var,
            bg="#0b0f19",
            fg="#f8fafc",
            relief=tk.FLAT,
            highlightbackground=theme_border,
            highlightcolor="#38bdf8",
            highlightthickness=1,
            font=("Segoe UI", 8),
        ).pack(fill=tk.X, pady=(0, 4))

        tk.Checkbutton(
            cam_card,
            text="🔄 Intercambiar: Panorámica en Primer Plano cuando nadie interactúe",
            variable=self.auto_swap_cam2_var,
            bg=theme_card,
            fg="#f8fafc",
            selectcolor="#0b0f19",
            activebackground=theme_card,
            font=("Segoe UI", 8),
            anchor="w",
        ).pack(fill=tk.X, pady=1)

        tk.Button(
            cam_card,
            text="🔄 Aplicar Configuración de Cámaras",
            command=self.start_camera_grabber,
            bg="#0284c7",
            fg="#ffffff",
            relief=tk.FLAT,
            font=("Segoe UI", 8, "bold"),
            pady=4,
            cursor="hand2",
        ).pack(fill=tk.X, pady=(4, 0))

        # --- TARJETA 4: ENFOQUE SUAVE & CINEMÁTICA IA ---
        ai_card = _make_card("👁️ 4. Zooms Dinámicos y Enfoque Suave IA", accent="#8b5cf6")

        tk.Checkbutton(
            ai_card,
            text="👁️ Zoom Dinámico Suave en Contacto Visual (1 a 1)",
            variable=self.dynamic_zoom_var,
            bg=theme_card,
            fg="#f8fafc",
            selectcolor="#0b0f19",
            activebackground=theme_card,
            font=("Segoe UI", 8),
            anchor="w",
        ).pack(fill=tk.X, pady=1)

        tk.Checkbutton(
            ai_card,
            text="🔄 Oscilación Suave (Izq ↔ Der) si no hay personas",
            variable=self.idle_patrol_var,
            bg=theme_card,
            fg="#f8fafc",
            selectcolor="#0b0f19",
            activebackground=theme_card,
            font=("Segoe UI", 8),
            anchor="w",
        ).pack(fill=tk.X, pady=1)

        tk.Checkbutton(
            ai_card,
            text="🪞 Modo Espejo (Imagen Invertida Natural)",
            variable=self.mirror_mode_var,
            bg=theme_card,
            fg="#f8fafc",
            selectcolor="#0b0f19",
            activebackground=theme_card,
            font=("Segoe UI", 8),
            anchor="w",
        ).pack(fill=tk.X, pady=1)

        tk.Checkbutton(
            ai_card,
            text="🖼️ Mostrar recuadro PiP de mi propia cámara en esquina",
            variable=self.pip_self_var,
            bg=theme_card,
            fg="#f8fafc",
            selectcolor="#0b0f19",
            activebackground=theme_card,
            font=("Segoe UI", 8),
            anchor="w",
        ).pack(fill=tk.X, pady=1)

        s_row = tk.Frame(ai_card, bg=theme_card)
        s_row.pack(fill=tk.X, pady=(4, 0))
        tk.Label(s_row, text="Suavidad de Enfoque:", bg=theme_card, fg="#94a3b8", font=("Segoe UI", 8)).pack(side=tk.LEFT)
        tk.Scale(
            s_row,
            from_=0.15,
            to=1.50,
            resolution=0.05,
            orient=tk.HORIZONTAL,
            variable=self.smooth_omega_var,
            bg=theme_card,
            fg="#34d399",
            highlightthickness=0,
            bd=0,
            troughcolor="#0b0f19",
            length=160,
        ).pack(side=tk.RIGHT)

        # --- BOTONES DE ACCIÓN ---
        btn_box = tk.Frame(win, bg="#0b0f19")
        btn_box.pack(fill=tk.X, pady=(6, 0))

        def _save_and_close():
            self.save_config()
            self._sync_audio_settings()
            win.destroy()

        tk.Button(
            btn_box,
            text="💾 Guardar Cambios",
            command=_save_and_close,
            bg="#059669",
            fg="#ffffff",
            relief=tk.FLAT,
            font=("Segoe UI", 9, "bold"),
            pady=6,
            cursor="hand2",
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))

        tk.Button(
            btn_box,
            text="Cerrar",
            command=win.destroy,
            bg="#1f2937",
            fg="#94a3b8",
            relief=tk.FLAT,
            font=("Segoe UI", 9),
            pady=6,
            padx=14,
            cursor="hand2",
        ).pack(side=tk.RIGHT)

        self.config_win = win

    def on_close(self):
        self.save_config()
        self.running = False
        if self.grabber1:
            self.grabber1.stop()
        if self.grabber2:
            self.grabber2.stop()
        if self.mesh_node:
            self.mesh_node.stop()
        self.root.destroy()


def main():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(base_dir)
    root = tk.Tk()
    app = PortalLiteApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
