import time
import math
import random
import threading
from dataclasses import dataclass, field
from typing import List, Dict, Tuple, Optional

import cv2
import numpy as np


@dataclass
class TrackedHandCursor:
    """Representa una mano/extremidad rastreada en coordenadas normalizadas (0.0 a 1.0) del Portal."""
    hand_id: int
    x: float  # 0.0 .. 1.0
    y: float  # 0.0 .. 1.0
    vx: float = 0.0
    vy: float = 0.0
    is_pinching: bool = False
    pinch_strength: float = 0.0  # 0.0 (abierta) .. 1.0 (pellizco cerrado)
    just_pinched: bool = False
    is_remote: bool = False
    player_side: str = "left"  # "left" o "right"
    landmarks_norm: List[Tuple[float, float]] = field(default_factory=list)
    trail: List[Tuple[float, float, float]] = field(default_factory=list)  # (x, y, timestamp)


class KinectGameEngine:
    """
    Motor de Juegos Interactivos Tipo Kinect para DeaCam Portal 24/7:
    - Botón flotante AR en pantalla ('🎮 JUEGOS KINECT') activable con Gesto de Pellizco (Pulgar + Índice).
    - Menú interactivo flotante Glassmorphism controlado por extremidades y gesto de pellizco.
    - Modos soportados:
        * 'solo'  : Solitario (1 Jugador vs IA / Récord)
        * 'multi' : Multijugador en Escena (2 o más jugadores frente a la cámara)
        * 'p2p'   : En Llamada Perpetua 24/7 (Portal Local vs Portal Remoto en Red)
    - 4 Juegos Kinect incluidos:
        1. 'pong'    : 🏓 Air Pong Neón (Raquetas con manos en vivo)
        2. 'slicer'  : 🍉 Neon Slicer Kinect (Corta esferas con estelas de manos/brazos)
        3. 'bubbles' : 🫧 Pinch Pop AR (Revienta burbujas holográficas con gesto de pellizco)
        4. 'paint'   : 🎨 Lienzo Aéreo Neón (Dibuja en el aire pellizcando con los dedos)
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.hud_enabled: bool = True
        # Estados: 'closed' (solo botón flotante), 'menu' (selector de juegos), 'playing' (en partida)
        self.state: str = "closed"
        self.active_game: str = "pong"  # 'pong', 'slicer', 'bubbles', 'paint'
        self.play_mode: str = "auto"    # 'auto', 'solo', 'multi', 'p2p'
        self.pinch_threshold: float = 0.62

        # Seguimiento suavizado de manos locales y remotas
        self._smooth_hands: Dict[int, TrackedHandCursor] = {}
        self._last_seen_hand: Dict[int, float] = {}
        self._hover_progress: Dict[str, float] = {}
        self._button_cooldown_until: float = 0.0

        # Cursores de simulación con mouse (para pruebas o uso con periféricos Admin)
        self._mouse_x: float = -1.0
        self._mouse_y: float = -1.0
        self._mouse_pinching: bool = False
        self._mouse_active_until: float = 0.0

        # Estado sincronizado del par remoto en llamada P2P
        self.remote_hands: List[Dict] = []
        self.remote_state_ts: float = 0.0

        # Puntajes globales
        self.score_left: int = 0
        self.score_right: int = 0
        self.combo_count: int = 0
        self.last_update_ts: float = time.time()

        # --- Estado Juego 1: Air Pong Neón ---
        self.ball_x: float = 0.5
        self.ball_y: float = 0.5
        self.ball_vx: float = 0.42
        self.ball_vy: float = 0.28
        self.paddle_left_y: float = 0.5
        self.paddle_right_y: float = 0.5
        self.pong_flash_ts: float = 0.0

        # --- Estado Juego 2: Neon Slicer Kinect ---
        self.slicer_orbs: List[Dict] = []
        self.slicer_particles: List[Dict] = []
        self.slicer_last_spawn: float = 0.0

        # --- Estado Juego 3: Pinch Pop Burbujas ---
        self.bubbles: List[Dict] = []
        self.bubbles_last_spawn: float = 0.0
        self.pop_effects: List[Dict] = []

        # --- Estado Juego 4: Lienzo Aéreo Neón ---
        self.paint_strokes: List[Dict] = []  # [{color: (b,g,r), points: [(x,y), ...], active: bool}]
        self.paint_color_idx: int = 0
        self.paint_palette: List[Tuple[str, Tuple[int, int, int]]] = [
            ("Cian Neón", (248, 189, 56)),
            ("Esmeralda", (129, 185, 16)),
            ("Rosa Neón", (94, 63, 244)),
            ("Violeta", (246, 92, 139)),
            ("Oro Solar", (11, 158, 245)),
            ("Blanco Luz", (250, 250, 250)),
        ]

    # =========================================================================
    # INTERACCIÓN CON MOUSE (OPCIONAL PARA ADMIN / PRUEBAS)
    # =========================================================================
    def set_mouse_cursor(self, norm_x: float, norm_y: float, is_pinching: bool):
        with self.lock:
            self._mouse_x = max(0.0, min(1.0, float(norm_x)))
            self._mouse_y = max(0.0, min(1.0, float(norm_y)))
            self._mouse_pinching = bool(is_pinching)
            self._mouse_active_until = time.time() + 2.5

    def open_menu(self):
        with self.lock:
            self.state = "menu"
            self._button_cooldown_until = time.time() + 0.45

    def close_games(self):
        with self.lock:
            self.state = "closed"
            self._button_cooldown_until = time.time() + 0.45

    def start_game(self, game_id: str, mode: Optional[str] = None):
        with self.lock:
            self.active_game = game_id
            if mode:
                self.play_mode = mode
            self.state = "playing"
            self.score_left = 0
            self.score_right = 0
            self.combo_count = 0
            self._reset_game_entities_unlocked()
            self._button_cooldown_until = time.time() + 0.55

    def _reset_game_entities_unlocked(self):
        self.ball_x, self.ball_y = 0.5, 0.5
        ang = random.choice([-0.55, -0.35, 0.35, 0.55])
        spd = 0.48
        self.ball_vx = random.choice([-1.0, 1.0]) * spd * math.cos(ang)
        self.ball_vy = spd * math.sin(ang)
        self.slicer_orbs.clear()
        self.slicer_particles.clear()
        self.bubbles.clear()
        self.pop_effects.clear()

    # =========================================================================
    # SINCRONIZACIÓN P2P ENTRE PORTALES EN LLAMADA 24/7
    # =========================================================================
    def export_local_sync_packet(self) -> Dict:
        """Exporta el estado de nuestras manos y juego actual para enviarlo al otro Portal en llamada P2P."""
        with self.lock:
            local_h = [
                {
                    "x": round(c.x, 4),
                    "y": round(c.y, 4),
                    "vx": round(c.vx, 3),
                    "vy": round(c.vy, 3),
                    "pinch": bool(c.is_pinching),
                    "str": round(c.pinch_strength, 2),
                }
                for hid, c in self._smooth_hands.items()
                if not c.is_remote
            ]
            pkt = {
                "state": self.state,
                "game": self.active_game,
                "mode": self.play_mode,
                "hands": local_h[:4],
                "score_l": self.score_left,
                "score_r": self.score_right,
                "ball": [round(self.ball_x, 4), round(self.ball_y, 4), round(self.ball_vx, 4), round(self.ball_vy, 4)],
                "ts": time.time(),
            }
            if self.active_game == "paint" and self.paint_strokes:
                pkt["strokes"] = self.paint_strokes[-12:]
            return pkt

    def ingest_remote_sync_packet(self, pkt: Dict):
        """Recibe los cursores de mano y estado de juego del Portal remoto conectado en llamada 24/7."""
        if not pkt or not isinstance(pkt, dict):
            return
        with self.lock:
            self.remote_hands = pkt.get("hands", [])
            self.remote_state_ts = time.time()
            rem_state = pkt.get("state", "closed")
            rem_game = pkt.get("game", "pong")
            # Si el otro Portal abrió un juego en modo P2P o Auto, sincronizar automáticamente
            if rem_state == "playing" and self.state == "closed" and self.hud_enabled:
                self.state = "playing"
                self.active_game = rem_game
                self.play_mode = "p2p"
            if rem_game == "paint" and "strokes" in pkt and self.active_game == "paint":
                # Mezclar trazos remotos si no tenemos trazos locales recientes
                if not self.paint_strokes and pkt["strokes"]:
                    self.paint_strokes = pkt["strokes"]

    # =========================================================================
    # DETECCIÓN DE MANOS, EXTREMIDADES Y GESTO DE PELLIZCO (PINCH)
    # =========================================================================
    def _update_tracked_hands(self, raw_hands: List[Dict], in_p2p_call: bool, dt: float) -> List[TrackedHandCursor]:
        now = time.time()
        active_cursors: List[TrackedHandCursor] = []
        used_ids = set()

        for idx, h in enumerate(raw_hands):
            cx = float(h.get("cx", 0.5))
            cy = float(h.get("cy", 0.5))
            pinch_ratio = float(h.get("pinch_ratio", 1.0))
            is_pinch = bool(h.get("is_pinching", False)) or (pinch_ratio < self.pinch_threshold)
            strength = max(0.0, min(1.0, (1.15 - pinch_ratio) / max(0.25, 1.15 - self.pinch_threshold)))
            lms_norm = h.get("landmarks_norm", [])

            # Asociar con mano existente más cercana para suavizar movimiento
            best_id = None
            best_dist = 0.22
            for hid, prev in self._smooth_hands.items():
                if prev.is_remote or hid in used_ids:
                    continue
                d = math.hypot(cx - prev.x, cy - prev.y)
                if d < best_dist:
                    best_dist = d
                    best_id = hid

            if best_id is None:
                best_id = idx + 1
                while best_id in used_ids or best_id >= 100:
                    best_id += 1
                cur = TrackedHandCursor(
                    hand_id=best_id,
                    x=cx,
                    y=cy,
                    is_pinching=is_pinch,
                    pinch_strength=strength,
                    just_pinched=is_pinch,
                    is_remote=False,
                    player_side="left" if cx < 0.5 else "right",
                    landmarks_norm=lms_norm,
                )
                self._smooth_hands[best_id] = cur
            else:
                cur = self._smooth_hands[best_id]
                alpha = 0.48
                new_x = cur.x + (cx - cur.x) * alpha
                new_y = cur.y + (cy - cur.y) * alpha
                cur.vx = (new_x - cur.x) / max(0.008, dt)
                cur.vy = (new_y - cur.y) / max(0.008, dt)
                cur.x = new_x
                cur.y = new_y
                cur.just_pinched = is_pinch and (not cur.is_pinching)
                cur.is_pinching = is_pinch
                cur.pinch_strength = cur.pinch_strength * 0.4 + strength * 0.6
                cur.player_side = "left" if cur.x < 0.5 else "right"
                cur.landmarks_norm = lms_norm

            cur.trail.append((cur.x, cur.y, now))
            cur.trail = [(tx, ty, ts) for (tx, ty, ts) in cur.trail if now - ts <= 0.35]

            used_ids.add(best_id)
            self._last_seen_hand[best_id] = now
            active_cursors.append(cur)

        # Agregar manos del jugador remoto si estamos en llamada P2P
        if in_p2p_call and (now - self.remote_state_ts < 2.5):
            for r_idx, rh in enumerate(self.remote_hands):
                rid = 100 + r_idx
                rx = float(rh.get("x", 0.5))
                ry = float(rh.get("y", 0.5))
                r_pinch = bool(rh.get("pinch", False))
                r_str = float(rh.get("str", 1.0 if r_pinch else 0.0))
                if rid not in self._smooth_hands:
                    rcur = TrackedHandCursor(
                        hand_id=rid,
                        x=rx,
                        y=ry,
                        vx=float(rh.get("vx", 0.0)),
                        vy=float(rh.get("vy", 0.0)),
                        is_pinching=r_pinch,
                        pinch_strength=r_str,
                        just_pinched=r_pinch,
                        is_remote=True,
                        player_side="right",
                    )
                    self._smooth_hands[rid] = rcur
                else:
                    rcur = self._smooth_hands[rid]
                    rcur.vx = float(rh.get("vx", 0.0))
                    rcur.vy = float(rh.get("vy", 0.0))
                    rcur.x = rcur.x * 0.4 + rx * 0.6
                    rcur.y = rcur.y * 0.4 + ry * 0.6
                    rcur.just_pinched = r_pinch and (not rcur.is_pinching)
                    rcur.is_pinching = r_pinch
                    rcur.pinch_strength = r_str
                rcur.trail.append((rcur.x, rcur.y, now))
                rcur.trail = [(tx, ty, ts) for (tx, ty, ts) in rcur.trail if now - ts <= 0.35]
                self._last_seen_hand[rid] = now
                active_cursors.append(rcur)

        # Soporte adicional de cursor por mouse (para pruebas o control desde Admin)
        if now < self._mouse_active_until and self._mouse_x >= 0.0:
            mid = 999
            prev_m = self._smooth_hands.get(mid)
            just_p = self._mouse_pinching and (prev_m is None or not prev_m.is_pinching)
            if prev_m is None:
                mcur = TrackedHandCursor(
                    hand_id=mid,
                    x=self._mouse_x,
                    y=self._mouse_y,
                    is_pinching=self._mouse_pinching,
                    pinch_strength=1.0 if self._mouse_pinching else 0.0,
                    just_pinched=just_p,
                    is_remote=False,
                    player_side="left" if self._mouse_x < 0.5 else "right",
                )
                self._smooth_hands[mid] = mcur
            else:
                mcur = prev_m
                mcur.vx = (self._mouse_x - mcur.x) / max(0.008, dt)
                mcur.vy = (self._mouse_y - mcur.y) / max(0.008, dt)
                mcur.x = self._mouse_x
                mcur.y = self._mouse_y
                mcur.just_pinched = just_p
                mcur.is_pinching = self._mouse_pinching
                mcur.pinch_strength = 1.0 if self._mouse_pinching else 0.0
            mcur.trail.append((mcur.x, mcur.y, now))
            mcur.trail = [(tx, ty, ts) for (tx, ty, ts) in mcur.trail if now - ts <= 0.35]
            self._last_seen_hand[mid] = now
            active_cursors.append(mcur)

        # Limpiar manos que ya salieron de escena
        stale = [k for k, ts in self._last_seen_hand.items() if now - ts > 0.45]
        for k in stale:
            self._last_seen_hand.pop(k, None)
            self._smooth_hands.pop(k, None)

        return active_cursors

    # =========================================================================
    # BOTONES AR GLASSMORPHISM CON SELECCIÓN POR PELLIZCO (PINCH) O HOVER
    # =========================================================================
    def _check_and_draw_ar_button(
        self,
        canvas: np.ndarray,
        btn_id: str,
        rect_norm: Tuple[float, float, float, float],
        title: str,
        subtitle: str,
        accent_bgr: Tuple[int, int, int],
        cursors: List[TrackedHandCursor],
        dt: float,
        active_highlight: bool = False,
    ) -> bool:
        """
        Dibuja un botón flotante estilo Glassmorphism en el video y devuelve True
        si el jugador hace el GESTO DE PELLIZCO sobre él (o mantiene la mano encima).
        """
        h, w = canvas.shape[:2]
        rx0, ry0, rx1, ry1 = rect_norm
        x0, y0 = int(rx0 * w), int(ry0 * h)
        x1, y1 = int(rx1 * w), int(ry1 * h)
        x0, y0 = max(2, x0), max(2, y0)
        x1, y1 = min(w - 2, x1), min(h - 2, y1)
        if x1 <= x0 + 10 or y1 <= y0 + 10:
            return False

        hovered = False
        pinched_inside = False
        max_pinch_str = 0.0

        for c in cursors:
            if rx0 <= c.x <= rx1 and ry0 <= c.y <= ry1:
                hovered = True
                max_pinch_str = max(max_pinch_str, c.pinch_strength)
                if c.is_pinching:
                    pinched_inside = True

        now = time.time()
        prog = self._hover_progress.get(btn_id, 0.0)
        if now < self._button_cooldown_until:
            prog = 0.0
        elif hovered:
            # Si hace gesto de pellizco, llena la barra casi instantáneamente (~0.18s);
            # si solo mantiene la mano abierta encima, carga suavemente (~1.05s)
            speed = 5.5 if pinched_inside else 0.95
            prog = min(1.0, prog + dt * speed)
        else:
            prog = max(0.0, prog - dt * 2.5)
        self._hover_progress[btn_id] = prog

        triggered = False
        if prog >= 1.0 and now >= self._button_cooldown_until:
            triggered = True
            self._hover_progress[btn_id] = 0.0
            self._button_cooldown_until = now + 0.65

        # Efecto Glassmorphism (oscurecer y desenfocar ligeramente el fondo del botón)
        roi = canvas[y0:y1, x0:x1]
        glass_bg = np.full_like(roi, (38, 22, 14) if not hovered else (58, 34, 22))
        alpha = 0.78 if (hovered or active_highlight) else 0.65
        cv2.addWeighted(glass_bg, alpha, roi, 1.0 - alpha, 0, roi)

        # Barra superior luminosa
        cv2.rectangle(canvas, (x0, y0), (x1, min(y1, y0 + 4)), accent_bgr, -1)

        # Barra de progreso de Pellizco / Selección en la parte inferior del botón
        if prog > 0.02:
            pw = int((x1 - x0) * prog)
            cv2.rectangle(canvas, (x0, max(y0, y1 - 6)), (x0 + pw, y1), (128, 230, 52), -1)

        border_col = (128, 255, 100) if pinched_inside else (accent_bgr if (hovered or active_highlight) else (110, 85, 65))
        thickness = 3 if (hovered or active_highlight) else 1
        cv2.rectangle(canvas, (x0, y0), (x1, y1), border_col, thickness, cv2.LINE_AA)

        # Textos centrados
        (tw, th), _ = cv2.getTextSize(title, cv2.FONT_HERSHEY_SIMPLEX, 0.56, 2)
        tx = x0 + max(8, (x1 - x0 - tw) // 2)
        ty = y0 + (y1 - y0) // 2 - (4 if subtitle else -6)
        cv2.putText(canvas, title, (tx, ty), cv2.FONT_HERSHEY_SIMPLEX, 0.56, (250, 252, 255), 2, cv2.LINE_AA)

        if subtitle:
            (sw, sh), _ = cv2.getTextSize(subtitle, cv2.FONT_HERSHEY_SIMPLEX, 0.40, 1)
            sx = x0 + max(6, (x1 - x0 - sw) // 2)
            sy = ty + sh + 10
            cv2.putText(canvas, subtitle, (sx, sy), cv2.FONT_HERSHEY_SIMPLEX, 0.40, (215, 225, 240), 1, cv2.LINE_AA)

        return triggered

    # =========================================================================
    # RENDERIZADO PRINCIPAL DE LA CAPA KINECT AR SOBRE EL VIDEO
    # =========================================================================
    def process_and_render(
        self,
        frame: np.ndarray,
        tracked_hands_raw: List[Dict],
        in_p2p_call: bool = False,
        persons_count: int = 1,
    ) -> np.ndarray:
        """
        Procesa los gestos de extremidades/pellizco y dibuja el botón flotante de juegos,
        el selector interactivo o el juego activo directamente sobre el frame.
        """
        with self.lock:
            now = time.time()
            dt = min(0.06, max(0.005, now - self.last_update_ts))
            self.last_update_ts = now

            if not self.hud_enabled:
                return frame

            cursors = self._update_tracked_hands(tracked_hands_raw, in_p2p_call, dt)

            # 1. ESTADO CERRADO: Mostrar únicamente el Botón Flotante Discreto en la parte superior
            if self.state == "closed":
                # Solo mostrar el botón flotante o iluminarlo cuando hay manos/personas o siempre accesible
                opened = self._check_and_draw_ar_button(
                    frame,
                    "btn_open_kinect_hub",
                    (0.36, 0.025, 0.64, 0.105),
                    "JUEGOS KINECT AR",
                    "Pellizca (Pulgar+Indice) aqui para abrir",
                    (248, 189, 56),  # Cyan BGR
                    cursors,
                    dt,
                )
                if opened:
                    self.state = "menu"
                self._draw_hand_cursors(frame, cursors)
                return frame

            # 2. ESTADO MENÚ: Selector Flotante Glassmorphism de Modo y Juegos
            if self.state == "menu":
                self._render_game_menu(frame, cursors, in_p2p_call, persons_count, dt)
                self._draw_hand_cursors(frame, cursors)
                return frame

            # 3. ESTADO JUGANDO: Ejecutar física del juego activo y HUD superior
            if self.state == "playing":
                self._render_active_game(frame, cursors, in_p2p_call, persons_count, dt)
                self._draw_hand_cursors(frame, cursors)
                return frame

            return frame

    # =========================================================================
    # MENÚ INTERACTIVO AR (SELECCIÓN CON GESTO DE PELLIZCO)
    # =========================================================================
    def _render_game_menu(
        self,
        frame: np.ndarray,
        cursors: List[TrackedHandCursor],
        in_p2p_call: bool,
        persons_count: int,
        dt: float,
    ):
        h, w = frame.shape[:2]

        # Panel central translúcido Glassmorphism
        px0, py0 = int(w * 0.12), int(h * 0.11)
        px1, py1 = int(w * 0.88), int(h * 0.89)
        roi = frame[py0:py1, px0:px1]
        dark_overlay = np.full_like(roi, (28, 16, 10))
        cv2.addWeighted(dark_overlay, 0.78, roi, 0.22, 0, roi)
        cv2.rectangle(frame, (px0, py0), (px1, py1), (248, 189, 56), 2, cv2.LINE_AA)
        cv2.rectangle(frame, (px0, py0), (px1, py0 + 5), (246, 92, 139), -1)

        # Título e instrucciones de Pellizco
        cv2.putText(
            frame,
            "CENTRO DE JUEGOS KINECT AR - CONTROL POR EXTREMIDADES Y PELLIZCO",
            (px0 + 28, py0 + 38),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.68,
            (248, 250, 252),
            2,
            cv2.LINE_AA,
        )
        mode_desc = (
            "Llamada P2P Conectada (Portal vs Portal)"
            if in_p2p_call
            else f"Jugadores detectados en camara: {max(1, persons_count)}"
        )
        cv2.putText(
            frame,
            f"Mueve tu mano sobre una tarjeta y haz el GESTO DE PELLIZCO (Pulgar + Indice) para seleccionar  |  {mode_desc}",
            (px0 + 28, py0 + 64),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.46,
            (180, 220, 255),
            1,
            cv2.LINE_AA,
        )

        # Fila 1: Selector de Modo de Jugadores (Solitario / Multijugador Local / En Llamada P2P)
        modes = [
            ("solo", "MODO SOLITARIO (1J)", "1 Jugador vs IA / Record", (180, 160, 60)),
            ("multi", "MULTIJUGADOR (2+ J)", "2 o mas jugadores en escena", (129, 185, 16)),
            ("p2p", "EN LLAMADA P2P 24/7", "Juega con el otro Portal", (246, 92, 139)),
        ]
        for idx, (m_id, m_title, m_sub, m_col) in enumerate(modes):
            bx0 = 0.15 + idx * 0.24
            bx1 = bx0 + 0.22
            if self._check_and_draw_ar_button(
                frame,
                f"mode_{m_id}",
                (bx0, 0.23, bx1, 0.34),
                m_title,
                m_sub,
                m_col,
                cursors,
                dt,
                active_highlight=(self.play_mode == m_id),
            ):
                self.play_mode = m_id

        # Fila 2 y 3: Las 4 Tarjetas de Juegos Tipo Kinect
        games_cards = [
            (
                "pong",
                "1. AIR PONG NEON",
                "Tus manos son raquetas de energia (1J / 2J / Llamada P2P)",
                (248, 189, 56),
                (0.15, 0.38, 0.48, 0.58),
            ),
            (
                "slicer",
                "2. NEON SLICER KINECT",
                "Corta esferas luminosas moviendo tus manos y brazos",
                (94, 63, 244),
                (0.52, 0.38, 0.85, 0.58),
            ),
            (
                "bubbles",
                "3. PINCH POP BURBUJAS",
                "Atrapa y revienta burbujas holograficas con un pellizco",
                (129, 185, 16),
                (0.15, 0.61, 0.48, 0.81),
            ),
            (
                "paint",
                "4. LIENZO AEREO NEON",
                "Pellizca en el aire para dibujar con luz en el espejo",
                (246, 92, 139),
                (0.52, 0.61, 0.85, 0.81),
            ),
        ]

        for g_id, g_title, g_sub, g_col, g_rect in games_cards:
            if self._check_and_draw_ar_button(
                frame,
                f"game_{g_id}",
                g_rect,
                g_title,
                g_sub,
                g_col,
                cursors,
                dt,
            ):
                self.active_game = g_id
                if self.play_mode == "auto":
                    self.play_mode = "p2p" if in_p2p_call else ("multi" if persons_count >= 2 else "solo")
                self.state = "playing"
                self.score_left = 0
                self.score_right = 0
                self.combo_count = 0
                self._reset_game_entities_unlocked()

        # Botón superior derecho para Cerrar el Menú
        if self._check_and_draw_ar_button(
            frame,
            "btn_close_menu",
            (0.74, 0.125, 0.86, 0.205),
            "X CERRAR",
            "Pellizca para salir",
            (94, 63, 244),
            cursors,
            dt,
        ):
            self.state = "closed"

    # =========================================================================
    # EJECUCIÓN Y RENDERIZADO DEL JUEGO ACTIVO
    # =========================================================================
    def _render_active_game(
        self,
        frame: np.ndarray,
        cursors: List[TrackedHandCursor],
        in_p2p_call: bool,
        persons_count: int,
        dt: float,
    ):
        # Barra superior HUD del juego con botón de Menú y botón de Salir
        if self._check_and_draw_ar_button(
            frame,
            "btn_back_to_menu",
            (0.02, 0.02, 0.18, 0.10),
            "MENU JUEGOS",
            "Pellizca aqui",
            (246, 92, 139),
            cursors,
            dt,
        ):
            self.state = "menu"
            return

        if self._check_and_draw_ar_button(
            frame,
            "btn_exit_game",
            (0.82, 0.02, 0.98, 0.10),
            "X SALIR JUEGO",
            "Pellizca aqui",
            (94, 63, 244),
            cursors,
            dt,
        ):
            self.state = "closed"
            return

        if self.active_game == "pong":
            self._step_and_draw_pong(frame, cursors, in_p2p_call, persons_count, dt)
        elif self.active_game == "slicer":
            self._step_and_draw_slicer(frame, cursors, dt)
        elif self.active_game == "bubbles":
            self._step_and_draw_bubbles(frame, cursors, dt)
        elif self.active_game == "paint":
            self._step_and_draw_paint(frame, cursors, dt)

    # -------------------------------------------------------------------------
    # JUEGO 1: AIR PONG NEÓN (1J vs IA / 2J Local / Llamada P2P)
    # -------------------------------------------------------------------------
    def _step_and_draw_pong(
        self,
        frame: np.ndarray,
        cursors: List[TrackedHandCursor],
        in_p2p_call: bool,
        persons_count: int,
        dt: float,
    ):
        h, w = frame.shape[:2]

        local_cursors = [c for c in cursors if not c.is_remote]
        remote_cursors = [c for c in cursors if c.is_remote]

        # Control de Raqueta Izquierda (Jugador 1 Local)
        left_candidates = [c for c in local_cursors if c.x < 0.52] or local_cursors
        if left_candidates:
            target_ly = sum(c.y for c in left_candidates) / len(left_candidates)
            self.paddle_left_y += (target_ly - self.paddle_left_y) * min(1.0, dt * 18.0)

        # Control de Raqueta Derecha (Jugador Remoto P2P, Jugador 2 Local en derecha, o IA en Solitario)
        right_local = [c for c in local_cursors if c.x >= 0.52]
        if remote_cursors:
            target_ry = sum(c.y for c in remote_cursors) / len(remote_cursors)
            self.paddle_right_y += (target_ry - self.paddle_right_y) * min(1.0, dt * 18.0)
            p2_label = "PORTAL REMOTO P2P"
        elif self.play_mode == "multi" or len(local_cursors) >= 2 or right_local:
            if right_local:
                target_ry = sum(c.y for c in right_local) / len(right_local)
                self.paddle_right_y += (target_ry - self.paddle_right_y) * min(1.0, dt * 18.0)
            p2_label = "JUGADOR 2 (DER)"
        else:
            # Guardián IA en modo Solitario
            ai_target = self.ball_y + math.sin(time.time() * 2.5) * 0.04
            self.paddle_right_y += (ai_target - self.paddle_right_y) * min(1.0, dt * 5.2)
            p2_label = "GUARDIAN IA"

        self.paddle_left_y = max(0.16, min(0.88, self.paddle_left_y))
        self.paddle_right_y = max(0.16, min(0.88, self.paddle_right_y))

        # Avanzar pelota de plasma
        self.ball_x += self.ball_vx * dt
        self.ball_y += self.ball_vy * dt

        # Rebote superior e inferior
        if self.ball_y < 0.14:
            self.ball_y = 0.14
            self.ball_vy = abs(self.ball_vy)
        elif self.ball_y > 0.92:
            self.ball_y = 0.92
            self.ball_vy = -abs(self.ball_vy)

        paddle_half_h = 0.115
        # Colisión con Raqueta Izquierda (x ~ 0.07)
        if self.ball_vx < 0 and 0.045 <= self.ball_x <= 0.095:
            if abs(self.ball_y - self.paddle_left_y) <= paddle_half_h + 0.025:
                self.ball_x = 0.095
                offset = (self.ball_y - self.paddle_left_y) / paddle_half_h
                spd = min(0.92, math.hypot(self.ball_vx, self.ball_vy) * 1.05)
                self.ball_vx = abs(spd * math.cos(offset * 0.65))
                self.ball_vy = spd * math.sin(offset * 0.65)
                self.pong_flash_ts = time.time()

        # Colisión con Raqueta Derecha (x ~ 0.93)
        if self.ball_vx > 0 and 0.905 <= self.ball_x <= 0.955:
            if abs(self.ball_y - self.paddle_right_y) <= paddle_half_h + 0.025:
                self.ball_x = 0.905
                offset = (self.ball_y - self.paddle_right_y) / paddle_half_h
                spd = min(0.92, math.hypot(self.ball_vx, self.ball_vy) * 1.05)
                self.ball_vx = -abs(spd * math.cos(offset * 0.65))
                self.ball_vy = spd * math.sin(offset * 0.65)
                self.pong_flash_ts = time.time()

        # También permitir golpear la pelota directamente con cualquier mano en el campo
        for c in cursors:
            if math.hypot(self.ball_x - c.x, self.ball_y - c.y) < 0.055:
                if c.x < 0.5 and self.ball_vx < 0:
                    self.ball_vx = abs(self.ball_vx) * 1.04
                    self.ball_vy += c.vy * 0.25
                elif c.x >= 0.5 and self.ball_vx > 0:
                    self.ball_vx = -abs(self.ball_vx) * 1.04
                    self.ball_vy += c.vy * 0.25

        # Goles
        if self.ball_x < 0.01:
            self.score_right += 1
            self._reset_game_entities_unlocked()
        elif self.ball_x > 0.99:
            self.score_left += 1
            self._reset_game_entities_unlocked()

        # Dibujar cancha neón
        cv2.line(frame, (w // 2, int(h * 0.12)), (w // 2, int(h * 0.94)), (80, 70, 50), 2, cv2.LINE_AA)
        cv2.circle(frame, (w // 2, int(h * 0.53)), int(min(w, h) * 0.12), (80, 70, 50), 2, cv2.LINE_AA)

        # Dibujar marcador central superior
        score_txt = f"JUGADOR 1: {self.score_left}   |   AIR PONG KINECT   |   {p2_label}: {self.score_right}"
        (tw, th), _ = cv2.getTextSize(score_txt, cv2.FONT_HERSHEY_SIMPLEX, 0.62, 2)
        sx = (w - tw) // 2
        cv2.rectangle(frame, (sx - 16, int(h * 0.02)), (sx + tw + 16, int(h * 0.095)), (35, 22, 14), -1)
        cv2.rectangle(frame, (sx - 16, int(h * 0.02)), (sx + tw + 16, int(h * 0.095)), (248, 189, 56), 2)
        cv2.putText(frame, score_txt, (sx, int(h * 0.068)), cv2.FONT_HERSHEY_SIMPLEX, 0.62, (248, 250, 252), 2, cv2.LINE_AA)

        # Dibujar Raquetas Neón
        ph_px = int(paddle_half_h * h)
        lx = int(0.07 * w)
        ly = int(self.paddle_left_y * h)
        cv2.rectangle(frame, (lx - 8, ly - ph_px), (lx + 8, ly + ph_px), (248, 189, 56), -1, cv2.LINE_AA)
        cv2.rectangle(frame, (lx - 11, ly - ph_px - 3), (lx + 11, ly + ph_px + 3), (255, 240, 180), 2, cv2.LINE_AA)

        rx = int(0.93 * w)
        ry = int(self.paddle_right_y * h)
        cv2.rectangle(frame, (rx - 8, ry - ph_px), (rx + 8, ry + ph_px), (246, 92, 139), -1, cv2.LINE_AA)
        cv2.rectangle(frame, (rx - 11, ry - ph_px - 3), (rx + 11, ry + ph_px + 3), (255, 200, 230), 2, cv2.LINE_AA)

        # Dibujar Pelota de Plasma con Halo
        bx, by = int(self.ball_x * w), int(self.ball_y * h)
        cv2.circle(frame, (bx, by), 22, (129, 185, 16), 2, cv2.LINE_AA)
        cv2.circle(frame, (bx, by), 14, (160, 245, 80), -1, cv2.LINE_AA)
        cv2.circle(frame, (bx, by), 7, (255, 255, 255), -1, cv2.LINE_AA)

    # -------------------------------------------------------------------------
    # JUEGO 2: NEON SLICER KINECT (CORTA ESFERAS CON EXTREMIDADES)
    # -------------------------------------------------------------------------
    def _step_and_draw_slicer(self, frame: np.ndarray, cursors: List[TrackedHandCursor], dt: float):
        h, w = frame.shape[:2]
        now = time.time()

        # Generar nuevas esferas cada ~0.75s
        if now - self.slicer_last_spawn > 0.72 and len(self.slicer_orbs) < 9:
            self.slicer_last_spawn = now
            is_bomb = random.random() < 0.16
            is_bonus = (not is_bomb) and (random.random() < 0.22)
            sx = random.uniform(0.18, 0.82)
            vx = (0.5 - sx) * random.uniform(0.35, 0.75) + random.uniform(-0.12, 0.12)
            vy = -random.uniform(0.68, 0.92)
            palette = [(248, 189, 56), (129, 185, 16), (246, 92, 139), (11, 158, 245)]
            self.slicer_orbs.append(
                {
                    "x": sx,
                    "y": 0.98,
                    "vx": vx,
                    "vy": vy,
                    "r": 0.046 if not is_bonus else 0.054,
                    "color": (60, 60, 255) if is_bomb else ((80, 235, 255) if is_bonus else random.choice(palette)),
                    "is_bomb": is_bomb,
                    "is_bonus": is_bonus,
                }
            )

        # Actualizar física de esferas y detectar corte por velocidad de mano o pellizco
        remaining_orbs = []
        for orb in self.slicer_orbs:
            orb["x"] += orb["vx"] * dt
            orb["y"] += orb["vy"] * dt
            orb["vy"] += 0.62 * dt  # gravedad suave

            sliced_by = None
            for c in cursors:
                dist = math.hypot(orb["x"] - c.x, orb["y"] - c.y)
                hand_spd = math.hypot(c.vx, c.vy)
                if dist <= (orb["r"] + 0.038) and (hand_spd > 0.18 or c.is_pinching or True):
                    sliced_by = c
                    break

            if sliced_by is not None:
                if orb["is_bomb"]:
                    self.score_left = max(0, self.score_left - 3)
                    self.combo_count = 0
                else:
                    pts = 3 if orb["is_bonus"] else 1
                    if sliced_by.player_side == "left" and not sliced_by.is_remote:
                        self.score_left += pts
                    else:
                        self.score_right += pts
                    self.combo_count += 1

                # Explosión de partículas neón
                for _ in range(10):
                    ang = random.uniform(0, math.pi * 2)
                    spd = random.uniform(0.15, 0.45)
                    self.slicer_particles.append(
                        {
                            "x": orb["x"],
                            "y": orb["y"],
                            "vx": math.cos(ang) * spd,
                            "vy": math.sin(ang) * spd,
                            "color": orb["color"],
                            "life": 0.45,
                        }
                    )
                continue

            if orb["y"] < 1.05:
                remaining_orbs.append(orb)
        self.slicer_orbs = remaining_orbs

        # Dibujar esferas
        for orb in self.slicer_orbs:
            ox, oy = int(orb["x"] * w), int(orb["y"] * h)
            rad = int(orb["r"] * min(w, h))
            col = orb["color"]
            cv2.circle(frame, (ox, oy), rad, col, -1, cv2.LINE_AA)
            cv2.circle(frame, (ox, oy), rad + 4, (255, 255, 255), 2, cv2.LINE_AA)
            lbl = "BOMBA" if orb["is_bomb"] else ("BONUS x3" if orb["is_bonus"] else "")
            if lbl:
                cv2.putText(frame, lbl, (ox - 26, oy + 5), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 2, cv2.LINE_AA)

        # Actualizar y dibujar partículas
        alive_p = []
        for pt in self.slicer_particles:
            pt["x"] += pt["vx"] * dt
            pt["y"] += pt["vy"] * dt
            pt["life"] -= dt
            if pt["life"] > 0:
                alive_p.append(pt)
                px, py = int(pt["x"] * w), int(pt["y"] * h)
                cv2.circle(frame, (px, py), max(2, int(pt["life"] * 14)), pt["color"], -1, cv2.LINE_AA)
        self.slicer_particles = alive_p

        # Marcador superior
        total_pts = self.score_left + self.score_right
        banner = f"NEON SLICER KINECT  |  PUNTOS: {total_pts} (J1: {self.score_left} | J2: {self.score_right})  |  COMBO: x{self.combo_count}"
        (tw, _), _ = cv2.getTextSize(banner, cv2.FONT_HERSHEY_SIMPLEX, 0.58, 2)
        sx = (w - tw) // 2
        cv2.rectangle(frame, (sx - 14, int(h * 0.02)), (sx + tw + 14, int(h * 0.095)), (35, 22, 14), -1)
        cv2.rectangle(frame, (sx - 14, int(h * 0.02)), (sx + tw + 14, int(h * 0.095)), (94, 63, 244), 2)
        cv2.putText(frame, banner, (sx, int(h * 0.068)), cv2.FONT_HERSHEY_SIMPLEX, 0.58, (248, 250, 252), 2, cv2.LINE_AA)

    # -------------------------------------------------------------------------
    # JUEGO 3: PINCH POP BURBUJAS (REVIENTA BURBUJAS CON GESTO DE PELLIZCO)
    # -------------------------------------------------------------------------
    def _step_and_draw_bubbles(self, frame: np.ndarray, cursors: List[TrackedHandCursor], dt: float):
        h, w = frame.shape[:2]
        now = time.time()

        if now - self.bubbles_last_spawn > 0.65 and len(self.bubbles) < 10:
            self.bubbles_last_spawn = now
            palette = [(248, 189, 56), (129, 185, 16), (246, 92, 139), (11, 158, 245)]
            self.bubbles.append(
                {
                    "x": random.uniform(0.14, 0.86),
                    "y": random.uniform(0.18, 0.84),
                    "r": random.uniform(0.042, 0.065),
                    "color": random.choice(palette),
                    "born": now,
                    "ttl": random.uniform(4.0, 6.5),
                }
            )

        remaining = []
        for b in self.bubbles:
            age = now - b["born"]
            if age > b["ttl"]:
                continue
            # Flotación suave
            b["y"] -= 0.025 * dt
            b["x"] += math.sin(now * 2.2 + b["born"]) * 0.015 * dt

            popped_by = None
            for c in cursors:
                dist = math.hypot(b["x"] - c.x, b["y"] - c.y)
                # Revienta al hacer Pellizco dentro de la burbuja (o al tocar su núcleo)
                if (dist <= b["r"] + 0.032 and c.is_pinching) or (dist <= b["r"] * 0.72):
                    popped_by = c
                    break

            if popped_by is not None:
                pts = 2 if popped_by.is_pinching else 1
                if popped_by.player_side == "left" and not popped_by.is_remote:
                    self.score_left += pts
                else:
                    self.score_right += pts
                self.pop_effects.append({"x": b["x"], "y": b["y"], "r": b["r"], "color": b["color"], "life": 0.35})
                continue

            remaining.append(b)
        self.bubbles = remaining

        # Dibujar burbujas holográficas
        for b in self.bubbles:
            bx, by = int(b["x"] * w), int(b["y"] * h)
            rad = int(b["r"] * min(w, h))
            col = b["color"]
            cv2.circle(frame, (bx, by), rad, col, 3, cv2.LINE_AA)
            cv2.circle(frame, (bx - rad // 3, by - rad // 3), max(3, rad // 5), (255, 255, 255), -1, cv2.LINE_AA)
            cv2.putText(frame, "PELLIZCA", (bx - 30, by + 5), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (240, 250, 255), 1, cv2.LINE_AA)

        alive_fx = []
        for fx in self.pop_effects:
            fx["life"] -= dt
            fx["r"] += dt * 0.12
            if fx["life"] > 0:
                alive_fx.append(fx)
                fx_x, fx_y = int(fx["x"] * w), int(fx["y"] * h)
                cv2.circle(frame, (fx_x, fx_y), int(fx["r"] * min(w, h)), fx["color"], 2, cv2.LINE_AA)
        self.pop_effects = alive_fx

        banner = f"PINCH POP AR (PELLIZCA LAS BURBUJAS)  |  J1: {self.score_left} pts  |  J2 / P2P: {self.score_right} pts"
        (tw, _), _ = cv2.getTextSize(banner, cv2.FONT_HERSHEY_SIMPLEX, 0.56, 2)
        sx = (w - tw) // 2
        cv2.rectangle(frame, (sx - 14, int(h * 0.02)), (sx + tw + 14, int(h * 0.095)), (35, 22, 14), -1)
        cv2.rectangle(frame, (sx - 14, int(h * 0.02)), (sx + tw + 14, int(h * 0.095)), (129, 185, 16), 2)
        cv2.putText(frame, banner, (sx, int(h * 0.068)), cv2.FONT_HERSHEY_SIMPLEX, 0.56, (248, 250, 252), 2, cv2.LINE_AA)

    # -------------------------------------------------------------------------
    # JUEGO 4: LIENZO AÉREO NEÓN (DIBUJA EN EL AIRE CON GESTO DE PELLIZCO)
    # -------------------------------------------------------------------------
    def _step_and_draw_paint(self, frame: np.ndarray, cursors: List[TrackedHandCursor], dt: float):
        h, w = frame.shape[:2]

        # Paleta flotante inferior para cambiar color o limpiar con un Pellizco
        for idx, (c_name, c_bgr) in enumerate(self.paint_palette):
            bx0 = 0.14 + idx * 0.105
            bx1 = bx0 + 0.095
            if self._check_and_draw_ar_button(
                frame,
                f"paint_col_{idx}",
                (bx0, 0.88, bx1, 0.96),
                c_name.split()[0],
                "",
                c_bgr,
                cursors,
                dt,
                active_highlight=(self.paint_color_idx == idx),
            ):
                self.paint_color_idx = idx

        if self._check_and_draw_ar_button(
            frame,
            "paint_clear",
            (0.78, 0.88, 0.92, 0.96),
            "LIMPIAR",
            "",
            (94, 63, 244),
            cursors,
            dt,
        ):
            self.paint_strokes.clear()

        cur_color = self.paint_palette[self.paint_color_idx][1]

        # Si cualquier mano hace Gesto de Pellizco fuera de las barras de botones, agrega puntos al trazo
        for c in cursors:
            if c.is_pinching and 0.12 < c.y < 0.86:
                if c.just_pinched or not self.paint_strokes or not self.paint_strokes[-1].get("active", False):
                    self.paint_strokes.append(
                        {
                            "color": cur_color if not c.is_remote else (246, 92, 139),
                            "points": [(c.x, c.y)],
                            "active": True,
                        }
                    )
                else:
                    pts = self.paint_strokes[-1]["points"]
                    if math.hypot(c.x - pts[-1][0], c.y - pts[-1][1]) > 0.004:
                        pts.append((c.x, c.y))
            else:
                if self.paint_strokes and self.paint_strokes[-1].get("active", False):
                    self.paint_strokes[-1]["active"] = False

        # Limitar memoria de trazos
        if len(self.paint_strokes) > 60:
            self.paint_strokes = self.paint_strokes[-60:]

        # Renderizar trazos con brillo neón
        for stroke in self.paint_strokes:
            pts = stroke.get("points", [])
            col = tuple(stroke.get("color", (248, 189, 56)))
            if len(pts) >= 2:
                arr = np.array([[int(px * w), int(py * h)] for (px, py) in pts], dtype=np.int32)
                cv2.polylines(frame, [arr], False, col, 8, cv2.LINE_AA)
                cv2.polylines(frame, [arr], False, (255, 255, 255), 2, cv2.LINE_AA)

        banner = "LIENZO AEREO KINECT: Junta Pulgar + Indice (Pellizco) y mueve tu mano para pintar en el espejo"
        (tw, _), _ = cv2.getTextSize(banner, cv2.FONT_HERSHEY_SIMPLEX, 0.50, 2)
        sx = (w - tw) // 2
        cv2.rectangle(frame, (sx - 12, int(h * 0.02)), (sx + tw + 12, int(h * 0.09)), (35, 22, 14), -1)
        cv2.rectangle(frame, (sx - 12, int(h * 0.02)), (sx + tw + 12, int(h * 0.09)), cur_color, 2)
        cv2.putText(frame, banner, (sx, int(h * 0.064)), cv2.FONT_HERSHEY_SIMPLEX, 0.50, (248, 250, 252), 2, cv2.LINE_AA)

    # =========================================================================
    # DIBUJO DE CURSORES DE MANO, ESQUELETO DE EXTREMIDADES E INDICADOR DE PELLIZCO
    # =========================================================================
    def _draw_hand_cursors(self, frame: np.ndarray, cursors: List[TrackedHandCursor]):
        h, w = frame.shape[:2]
        for c in cursors:
            cx, cy = int(c.x * w), int(c.y * h)
            base_col = (246, 92, 139) if c.is_remote else (248, 189, 56)

            # Estela luminosa de movimiento (Katana / Kinect Trail)
            if len(c.trail) >= 2:
                for i in range(1, len(c.trail)):
                    x0, y0, _ = c.trail[i - 1]
                    x1, y1, _ = c.trail[i]
                    thickness = max(1, int((i / len(c.trail)) * 6))
                    cv2.line(
                        frame,
                        (int(x0 * w), int(y0 * h)),
                        (int(x1 * w), int(y1 * h)),
                        (129, 235, 60) if c.is_pinching else base_col,
                        thickness,
                        cv2.LINE_AA,
                    )

            # Si tenemos las 21 articulaciones de la mano, dibujar conexión Pulgar(4) <-> Índice(8)
            if len(c.landmarks_norm) >= 21:
                tx, ty = int(c.landmarks_norm[4][0] * w), int(c.landmarks_norm[4][1] * h)
                ix, iy = int(c.landmarks_norm[8][0] * w), int(c.landmarks_norm[8][1] * h)
                line_col = (100, 255, 100) if c.is_pinching else (0, 215, 255)
                cv2.line(frame, (tx, ty), (ix, iy), line_col, 2, cv2.LINE_AA)
                cv2.circle(frame, (tx, ty), 5, line_col, -1, cv2.LINE_AA)
                cv2.circle(frame, (ix, iy), 5, line_col, -1, cv2.LINE_AA)

            # Anillo de cursor Kinect (se contrae e ilumina en verde esmeralda al hacer Pellizco)
            outer_r = int(28 - c.pinch_strength * 12)
            ring_col = (100, 255, 120) if c.is_pinching else base_col
            cv2.circle(frame, (cx, cy), max(10, outer_r), ring_col, 2, cv2.LINE_AA)
            cv2.circle(frame, (cx, cy), 6 if c.is_pinching else 4, (255, 255, 255), -1, cv2.LINE_AA)

            if c.is_pinching:
                cv2.putText(
                    frame,
                    "PELLIZCO",
                    (cx + 14, cy - 10),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.42,
                    (100, 255, 120),
                    2,
                    cv2.LINE_AA,
                )
