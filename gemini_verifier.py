import os
import time
import json
import base64
import hashlib
import threading
from typing import Dict, Optional, Tuple, Callable

import cv2
import numpy as np
import requests


class GeminiPointVerifier:
    """
    Motor de IA Interpretativa Puntual con Gemini Vision API (Ultra-Bajo Consumo de Tokens):
    - NO se ejecuta el 100% del tiempo: la IA local (YOLOX + YuNet + 21 Puntos 3D) procesa el 99.9% de los cuadros gratis.
    - Gemini entra ÚNICAMENTE en áreas de oportunidad puntuales seleccionadas por el Administrador:
        1. Desempate / Verificación de Actos Ofensivos o Uso de Celular en "Zona Gris" (evita falsos positivos y detecta actos sutiles).
        2. Dictamen Forense Interpretativo al capturar una Evidencia de Mal Uso.
        3. Enriquecimiento de Perfil Humano 1 sola vez cuando una persona nueva establece Conexión 1 a 1.
        4. Consulta puntual manual desde el botón del Panel Admin.
    - Optimizaciones extremas de Tokens:
        * Recorta y redimensiona la región de interés a 384x384 JPEG calidad 72% (~258 tokens por imagen).
        * Caché visual por huella perceptual (evita volver a consultar la misma postura/persona).
        * Cooldown configurable (ej. 30s) y Tope Máximo de Llamadas por Hora (Rate Limiter).
        * Salida JSON ultra-compacta (maxOutputTokens=160).
    """

    def __init__(self, api_key: str = "", model_name: str = "gemini-2.5-flash"):
        self.api_key: str = api_key or os.environ.get("GEMINI_API_KEY", "")
        self.model_name: str = model_name
        self.enabled: bool = False

        # Disparadores puntuales (Áreas de oportunidad)
        self.trigger_on_ambiguous_or_offense: bool = True
        self.trigger_on_new_1to1_person: bool = True

        # Guardrails de ahorro de tokens
        self.cooldown_seconds: float = 30.0
        self.max_calls_per_hour: int = 30

        # Métricas de consumo y estado
        self.lock = threading.Lock()
        self.calls_this_hour: int = 0
        self.total_calls_session: int = 0
        self.estimated_tokens_session: int = 0
        self.hour_window_start: float = time.time()
        self.last_call_ts: float = 0.0
        self.busy: bool = False

        # Último resultado interpretativo para mostrar en UI / Inspector Biométrico
        self.last_interpretation: Dict = {
            "timestamp": "--:--:--",
            "trigger_reason": "En espera de evento puntual",
            "is_offensive_confirmed": False,
            "act_detected": "Ninguno",
            "context_summary": "IA Local activa al 100%. Gemini listo solo para casos puntuales.",
            "attire_and_attitude": "--",
            "confidence": 0.0,
        }

        # Caché por persona/escena para no repetir llamadas sobre la misma persona
        self._person_enriched_cache: Dict[int, Tuple[float, Dict]] = {}
        self._visual_hash_cache: Dict[str, Tuple[float, Dict]] = {}

    def configure(
        self,
        api_key: str,
        model_name: str = "gemini-2.5-flash",
        enabled: bool = False,
        trigger_offense: bool = True,
        trigger_1to1: bool = True,
        cooldown_sec: float = 30.0,
        max_per_hour: int = 30,
    ):
        with self.lock:
            self.api_key = (api_key or os.environ.get("GEMINI_API_KEY", "")).strip()
            # Extraer ID limpio del modelo si viene con etiqueta descriptiva del Combobox
            clean_model = model_name.split()[0].strip() if model_name else "gemini-2.5-flash"
            self.model_name = clean_model
            self.enabled = bool(enabled)
            self.trigger_on_ambiguous_or_offense = bool(trigger_offense)
            self.trigger_on_new_1to1_person = bool(trigger_1to1)
            self.cooldown_seconds = max(5.0, float(cooldown_sec))
            self.max_calls_per_hour = max(1, int(max_per_hour))

    def can_make_auto_call(self) -> Tuple[bool, str]:
        """Verifica si podemos hacer una llamada puntual automática respetando el presupuesto de tokens."""
        with self.lock:
            if not self.enabled:
                return False, "Gemini desactivado"
            if not self.api_key:
                return False, "Falta API Key de Gemini"
            if self.busy:
                return False, "Consulta en curso"

            now = time.time()
            if now - self.hour_window_start >= 3600.0:
                self.hour_window_start = now
                self.calls_this_hour = 0

            if self.calls_this_hour >= self.max_calls_per_hour:
                return False, f"Tope de ahorro alcanzado ({self.max_calls_per_hour}/hora)"

            if (now - self.last_call_ts) < self.cooldown_seconds:
                rem = int(self.cooldown_seconds - (now - self.last_call_ts))
                return False, f"Enfriamiento de ahorro ({rem}s)"

            return True, "OK"

    def _encode_compact_roi(
        self,
        frame_bgr: np.ndarray,
        bbox: Optional[Tuple[int, int, int, int]] = None,
    ) -> Tuple[str, str]:
        """
        Recorta solo el área relevante y la comprime a máximo 384x384 JPEG calidad 72%
        para minimizar al máximo los tokens de entrada (~258 tokens) y calcula un hash visual.
        """
        h, w = frame_bgr.shape[:2]
        if bbox is not None:
            bx, by, bw, bh = bbox
            pad_x = int(bw * 0.35)
            pad_y = int(bh * 0.35)
            x0 = max(0, bx - pad_x)
            y0 = max(0, by - pad_y)
            x1 = min(w, bx + bw + pad_x)
            y1 = min(h, by + bh + pad_y)
            crop = frame_bgr[y0:y1, x0:x1] if (x1 > x0 + 20 and y1 > y0 + 20) else frame_bgr
        else:
            crop = frame_bgr

        ch, cw = crop.shape[:2]
        max_dim = 384
        if max(ch, cw) > max_dim:
            scale = max_dim / float(max(ch, cw))
            crop = cv2.resize(crop, (max(16, int(cw * scale)), max(16, int(ch * scale))), interpolation=cv2.INTER_AREA)

        # Huella visual rápida de 8x8 para evitar gastar tokens en frames casi idénticos
        tiny = cv2.resize(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY), (8, 8), interpolation=cv2.INTER_AREA)
        avg = float(np.mean(tiny))
        bits = "".join("1" if px > avg else "0" for px in tiny.flatten())
        v_hash = hashlib.md5(bits.encode("ascii")).hexdigest()[:12]

        ok, buf = cv2.imencode(".jpg", crop, [int(cv2.IMWRITE_JPEG_QUALITY), 72])
        b64_str = base64.b64encode(buf.tobytes()).decode("ascii") if ok else ""
        return b64_str, v_hash

    def request_point_analysis_async(
        self,
        frame_bgr: np.ndarray,
        trigger_reason: str = "Análisis Puntual",
        bbox: Optional[Tuple[int, int, int, int]] = None,
        person_id: Optional[int] = None,
        force_manual: bool = False,
        on_complete: Optional[Callable[[Dict], None]] = None,
    ) -> bool:
        """
        Lanza un análisis interpretativo puntual en segundo plano sin bloquear el video:
        - Si force_manual=False, respeta estrictamente el Cooldown, la Cuota por Hora y el Caché Visual.
        """
        if frame_bgr is None:
            return False

        if not force_manual:
            can_call, _ = self.can_make_auto_call()
            if not can_call:
                return False
            if person_id is not None and person_id in self._person_enriched_cache:
                ts_cached, _ = self._person_enriched_cache[person_id]
                if time.time() - ts_cached < 180.0:
                    return False
        else:
            with self.lock:
                if not self.api_key:
                    self.last_interpretation = {
                        "timestamp": time.strftime("%H:%M:%S"),
                        "trigger_reason": "Error de Configuración",
                        "is_offensive_confirmed": False,
                        "act_detected": "Sin API Key",
                        "context_summary": "Ingresa tu GEMINI_API_KEY en el Panel Admin para usar la IA interpretativa puntual.",
                        "attire_and_attitude": "--",
                        "confidence": 0.0,
                    }
                    if on_complete:
                        on_complete(self.last_interpretation)
                    return False
                if self.busy:
                    return False

        frame_copy = frame_bgr.copy()

        def _worker():
            with self.lock:
                self.busy = True
                self.last_call_ts = time.time()

            try:
                b64_img, v_hash = self._encode_compact_roi(frame_copy, bbox)
                if not b64_img:
                    return

                # Revisar caché visual si no es manual forzado
                now = time.time()
                if not force_manual and v_hash in self._visual_hash_cache:
                    c_ts, c_res = self._visual_hash_cache[v_hash]
                    if now - c_ts < 90.0:
                        with self.lock:
                            self.last_interpretation = c_res
                        if on_complete:
                            on_complete(c_res)
                        return

                prompt = (
                    "Eres el auditor visual experto de un Portal público interactivo 24/7. "
                    "Analiza esta imagen de forma concisa y responde EXCLUSIVAMENTE en JSON válido con estas claves exactas: "
                    '{"is_offensive_confirmed": bool, '
                    '"act_detected": "Ninguno" o nombre del acto (ej. "Dedo medio", "Seña obscena", "Tomando foto con celular", "Amenaza"), '
                    '"context_summary": "1 oración breve en español explicando qué hace realmente la persona y si es un falso positivo o acto real", '
                    '"attire_and_attitude": "Descripción breve de vestimenta, actitud/expresión e intención", '
                    '"confidence": float entre 0.0 y 1.0}'
                )

                url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model_name}:generateContent?key={self.api_key}"
                payload = {
                    "contents": [
                        {
                            "parts": [
                                {"text": prompt},
                                {
                                    "inline_data": {
                                        "mime_type": "image/jpeg",
                                        "data": b64_img,
                                    }
                                },
                            ]
                        }
                    ],
                    "generationConfig": {
                        "temperature": 0.1,
                        "maxOutputTokens": 160,
                        "responseMimeType": "application/json",
                    },
                }

                resp = requests.post(url, json=payload, timeout=6.5)
                if resp.status_code == 200:
                    data = resp.json()
                    usage = data.get("usageMetadata", {})
                    used_tok = int(usage.get("totalTokenCount", 340))
                    candidates = data.get("candidates", [])
                    raw_txt = "{}"
                    if candidates:
                        parts = candidates[0].get("content", {}).get("parts", [])
                        if parts:
                            raw_txt = parts[0].get("text", "{}")

                    parsed = json.loads(raw_txt)
                    result = {
                        "timestamp": time.strftime("%H:%M:%S"),
                        "trigger_reason": trigger_reason,
                        "is_offensive_confirmed": bool(parsed.get("is_offensive_confirmed", False)),
                        "act_detected": str(parsed.get("act_detected", "Ninguno")),
                        "context_summary": str(parsed.get("context_summary", "Escena verificada por Gemini.")),
                        "attire_and_attitude": str(parsed.get("attire_and_attitude", "Normal")),
                        "confidence": float(parsed.get("confidence", 0.9)),
                    }

                    with self.lock:
                        self.calls_this_hour += 1
                        self.total_calls_session += 1
                        self.estimated_tokens_session += used_tok
                        self.last_interpretation = result
                        self._visual_hash_cache[v_hash] = (time.time(), result)
                        if person_id is not None:
                            self._person_enriched_cache[person_id] = (time.time(), result)

                    if on_complete:
                        on_complete(result)
                else:
                    err_msg = f"HTTP {resp.status_code}: {resp.text[:90]}"
                    with self.lock:
                        self.last_interpretation = {
                            "timestamp": time.strftime("%H:%M:%S"),
                            "trigger_reason": trigger_reason,
                            "is_offensive_confirmed": False,
                            "act_detected": "Aviso API",
                            "context_summary": err_msg,
                            "attire_and_attitude": "--",
                            "confidence": 0.0,
                        }
            except Exception as e:
                with self.lock:
                    self.last_interpretation = {
                        "timestamp": time.strftime("%H:%M:%S"),
                        "trigger_reason": trigger_reason,
                        "is_offensive_confirmed": False,
                        "act_detected": "Error de red",
                        "context_summary": f"No se pudo contactar Gemini API: {str(e)[:80]}",
                        "attire_and_attitude": "--",
                        "confidence": 0.0,
                    }
            finally:
                with self.lock:
                    self.busy = False

        threading.Thread(target=_worker, daemon=True).start()
        return True

    def get_usage_summary(self) -> str:
        with self.lock:
            # Costo estimado en gemini-2.5-flash (~$0.15 / 1M tokens)
            cost_usd = (self.estimated_tokens_session / 1_000_000.0) * 0.18
            return (
                f"Llamadas: {self.calls_this_hour}/{self.max_calls_per_hour} hr "
                f"(Total: {self.total_calls_session}) | "
                f"Tokens: ~{self.estimated_tokens_session} (${cost_usd:.5f} USD)"
            )
