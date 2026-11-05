import os
import time
import json
import queue
import socket
import struct
import threading
from typing import Dict, List, Optional, Tuple, Callable

import cv2
import numpy as np

try:
    import sounddevice as sd
    HAS_SOUNDDEVICE = True
except Exception:
    sd = None
    HAS_SOUNDDEVICE = False


def get_local_lan_ips() -> List[str]:
    """Devuelve todas las direcciones IPv4 LAN activas de este equipo."""
    ips = []
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if ip and not ip.startswith("127.") and ip not in ips:
                ips.append(ip)
    except Exception:
        pass

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.3)
        s.connect(("8.8.8.8", 80))
        primary_ip = s.getsockname()[0]
        s.close()
        if primary_ip and not primary_ip.startswith("127."):
            if primary_ip in ips:
                ips.remove(primary_ip)
            ips.insert(0, primary_ip)
    except Exception:
        pass

    if not ips:
        ips.append("127.0.0.1")
    return ips


def discover_usb_cameras(max_index: int = 4) -> List[Dict[str, str]]:
    """
    Detecta cámaras USB / Webcams / Capturadoras de video locales conectadas al equipo
    (compatible con Windows DirectShow y Raspberry Pi / Linux V4L2).
    """
    found = []
    preferred_backend = cv2.CAP_DSHOW if os.name == "nt" else getattr(cv2, "CAP_V4L2", cv2.CAP_ANY)
    for idx in range(max_index):
        cap = None
        try:
            cap = cv2.VideoCapture(idx, preferred_backend)
            if not (cap and cap.isOpened()):
                if cap:
                    cap.release()
                cap = cv2.VideoCapture(idx, cv2.CAP_ANY)
            if cap and cap.isOpened():
                w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 640)
                h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 480)
                found.append({
                    "index": idx,
                    "label": f"USB {idx}: Cámara Local / Webcam ({w}x{h})",
                })
        except Exception:
            pass
        finally:
            if cap:
                try:
                    cap.release()
                except Exception:
                    pass
    if not found:
        found.append({"index": 0, "label": "USB 0: Cámara Local / Webcam Predeterminada"})
    return found


class UniversalCameraGrabber:
    """
    Lector Universal de Cámara de Cero Latencia (Bufferless Grabber):
    Compatible con:
      1. Cámaras IP Dahua PoE (RTSP Dual o Simple)
      2. Cualquier cámara USB / Webcam / Capturadora HDMI local (DirectShow / MSMF)
      3. Cualquier cámara IP Universal por URL RTSP / HTTP / ONVIF / MJPEG
    Drena continuamente el flujo en segundo plano para evitar acumulación de buffer.
    """

    def __init__(
        self,
        source_spec: str,
        ip_hint: str = "",
        mirror_fn: Optional[Callable[[], bool]] = None,
        source_type: str = "rtsp",
    ):
        """
        source_spec:
          - Para USB: "0", "1", "USB:0", etc.
          - Para RTSP/HTTP/Dahua: "rtsp://..." o "http://..."
        """
        self.source_spec = str(source_spec).strip()
        self.ip_hint = str(ip_hint).strip()
        self.mirror_fn = mirror_fn
        self.source_type = source_type
        self.running = True
        self.cap = None
        self.latest_frame = None
        self.frame_seq = 0
        self.connected = False
        self.lock = threading.Lock()
        self.thread = threading.Thread(target=self._grab_loop, daemon=True)
        self.thread.start()

    def _is_usb_source(self) -> Tuple[bool, int]:
        spec = self.source_spec.upper()
        if self.source_type == "usb" or spec.startswith("USB:") or spec.isdigit():
             digits = "".join(ch for ch in spec if ch.isdigit())
             return True, int(digits) if digits else 0
        return False, 0

    def _extract_host_port(self) -> Tuple[str, int]:
        if self.ip_hint:
            return self.ip_hint, 554
        url = self.source_spec
        try:
            if "://" in url:
                rest = url.split("://", 1)[1]
                if "@" in rest:
                    rest = rest.split("@", 1)[1]
                host_port = rest.split("/", 1)[0]
                if ":" in host_port:
                    h, p = host_port.split(":", 1)
                    return h, int(p)
                return host_port, (80 if url.lower().startswith("http:") else 554)
        except Exception:
            pass
        return "", 554

    def _test_tcp(self) -> bool:
        host, port = self._extract_host_port()
        if not host:
            return True
        try:
            with socket.create_connection((host, port), timeout=1.0):
                return True
        except Exception:
            return False

    def _open_capture(self):
        is_usb, usb_idx = self._is_usb_source()
        if is_usb:
            preferred_backend = cv2.CAP_DSHOW if os.name == "nt" else getattr(cv2, "CAP_V4L2", cv2.CAP_ANY)
            cap = cv2.VideoCapture(usb_idx, preferred_backend)
            if not (cap and cap.isOpened()):
                if cap:
                    cap.release()
                cap = cv2.VideoCapture(usb_idx, cv2.CAP_ANY)
            if cap and cap.isOpened():
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
                cap.set(cv2.CAP_PROP_FPS, 30)
                return cap
            if cap:
                cap.release()
            return None
        else:
            if not self._test_tcp():
                return None
            cap = cv2.VideoCapture(self.source_spec, cv2.CAP_FFMPEG)
            if cap and cap.isOpened():
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                return cap
            if cap:
                cap.release()
            return None

    def _grab_loop(self):
        while self.running:
            if self.cap is None or not self.cap.isOpened():
                self.connected = False
                cap = self._open_capture()
                if cap is not None:
                    self.cap = cap
                    self.connected = True
                else:
                    time.sleep(1.0)
                    continue

            ret, frame = self.cap.read()
            if not ret or frame is None:
                self.connected = False
                try:
                    self.cap.release()
                except Exception:
                    pass
                self.cap = None
                time.sleep(0.2)
                continue

            if self.mirror_fn is not None and self.mirror_fn():
                frame = cv2.flip(frame, 1)

            with self.lock:
                self.latest_frame = frame
                self.frame_seq += 1
                self.connected = True

        if self.cap:
            try:
                self.cap.release()
            except Exception:
                pass

    def get_latest(self):
        with self.lock:
            return self.latest_frame, self.frame_seq, self.connected

    def stop(self):
        self.running = False


class AudioStreamEngine:
    """
    Motor de Audio Bidireccional de Baja Latencia (Micrófono y Parlante / Altavoz Universal):
    - Enumera todos los dispositivos de entrada (Micrófono) y salida (Parlante) de Windows.
    - Captura PCM 16-bit 16kHz Mono del micrófono seleccionado y lo envía por callback a la llamada P2P.
    - Reproduce en tiempo real en el parlante seleccionado los paquetes de audio recibidos de la red P2P.
    """

    SAMPLE_RATE = 16000
    CHANNELS = 1
    BLOCK_SAMPLES = 640  # 40ms por paquete (baja latencia y estable en LAN)

    def __init__(self, on_local_audio_chunk: Optional[Callable[[bytes], None]] = None):
        self.on_local_audio_chunk = on_local_audio_chunk
        self.running = False
        self.input_device_index: Optional[int] = None
        self.output_device_index: Optional[int] = None
        self.mic_muted = False
        self.speaker_muted = False
        self.mic_gain = 1.0
        self.speaker_volume = 1.0
        self.mic_level = 0.0
        self.speaker_level = 0.0

        self.play_queue: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=25)
        self._in_stream = None
        self._out_stream = None
        self._lock = threading.Lock()

    @staticmethod
    def list_audio_devices() -> Tuple[List[Dict], List[Dict]]:
        """
        Devuelve (input_devices, output_devices) con formato:
        [{"index": None, "label": "Predeterminado del Sistema"}, {"index": i, "label": "..."}]
        """
        inputs = [{"index": None, "label": "🎤 Micrófono Predeterminado del Sistema"}]
        outputs = [{"index": None, "label": "🔊 Parlante / Altavoz Predeterminado del Sistema"}]

        if not HAS_SOUNDDEVICE:
            return inputs, outputs

        try:
            devs = sd.query_devices()
            hostapis = sd.query_hostapis()
            # Preferir MME / Windows DirectSound / WASAPI sin duplicar nombres excesivos
            seen_in = set()
            seen_out = set()
            for idx, d in enumerate(devs):
                name = str(d.get("name", f"Dispositivo {idx}")).strip()
                max_in = int(d.get("max_input_channels", 0))
                max_out = int(d.get("max_output_channels", 0))
                hostapi_idx = int(d.get("hostapi", 0))
                api_name = hostapis[hostapi_idx].get("name", "") if hostapi_idx < len(hostapis) else ""
                # Priorizar MME (hostapi 0 en Windows) para compatibilidad universal de tasa de muestreo
                if "MME" in api_name or hostapi_idx == 0:
                    if max_in > 0 and name not in seen_in:
                        seen_in.add(name)
                        inputs.append({"index": idx, "label": f"[{idx}] 🎤 {name}"})
                    if max_out > 0 and name not in seen_out:
                        seen_out.add(name)
                        outputs.append({"index": idx, "label": f"[{idx}] 🔊 {name}"})
            # Si no hubo MME, agregar todos
            if len(inputs) == 1 and len(outputs) == 1:
                for idx, d in enumerate(devs):
                    name = str(d.get("name", f"Dispositivo {idx}")).strip()
                    if int(d.get("max_input_channels", 0)) > 0:
                        inputs.append({"index": idx, "label": f"[{idx}] 🎤 {name}"})
                    if int(d.get("max_output_channels", 0)) > 0:
                        outputs.append({"index": idx, "label": f"[{idx}] 🔊 {name}"})
        except Exception:
            pass

        return inputs, outputs

    def configure_devices(self, input_idx: Optional[int], output_idx: Optional[int]):
        restart_needed = (
            self.running
            and (input_idx != self.input_device_index or output_idx != self.output_device_index)
        )
        self.input_device_index = input_idx
        self.output_device_index = output_idx
        if restart_needed:
            self.stop()
            self.start()

    def start(self):
        if not HAS_SOUNDDEVICE:
            return
        with self._lock:
            if self.running:
                return
            self.running = True
            self._start_streams_unlocked()

    def _start_streams_unlocked(self):
        # Iniciar flujo de entrada (Micrófono)
        try:
            self._in_stream = sd.InputStream(
                samplerate=self.SAMPLE_RATE,
                blocksize=self.BLOCK_SAMPLES,
                device=self.input_device_index,
                channels=self.CHANNELS,
                dtype="int16",
                callback=self._input_callback,
            )
            self._in_stream.start()
        except Exception:
            self._in_stream = None

        # Iniciar flujo de salida (Parlante / Altavoz)
        try:
            self._out_stream = sd.OutputStream(
                samplerate=self.SAMPLE_RATE,
                blocksize=self.BLOCK_SAMPLES,
                device=self.output_device_index,
                channels=self.CHANNELS,
                dtype="int16",
                callback=self._output_callback,
            )
            self._out_stream.start()
        except Exception:
            self._out_stream = None

    def _input_callback(self, indata, frames, time_info, status):
        if not self.running or self.mic_muted:
            self.mic_level = 0.0
            return
        try:
            arr = indata[:, 0].astype(np.float32)
            if abs(self.mic_gain - 1.0) > 0.02:
                arr = np.clip(arr * self.mic_gain, -32768, 32767)
            rms = float(np.sqrt(np.mean(arr * arr))) / 32768.0
            self.mic_level = min(1.0, rms * 4.5)
            if self.on_local_audio_chunk is not None:
                pcm_bytes = arr.astype(np.int16).tobytes()
                self.on_local_audio_chunk(pcm_bytes)
        except Exception:
            pass

    def _output_callback(self, outdata, frames, time_info, status):
        if not self.running or self.speaker_muted:
            outdata.fill(0)
            self.speaker_level = 0.0
            return
        try:
            chunk = self.play_queue.get_nowait()
            if len(chunk) < frames:
                padded = np.zeros(frames, dtype=np.int16)
                padded[: len(chunk)] = chunk
                chunk = padded
            elif len(chunk) > frames:
                chunk = chunk[:frames]
            if abs(self.speaker_volume - 1.0) > 0.02:
                scaled = np.clip(chunk.astype(np.float32) * self.speaker_volume, -32768, 32767).astype(np.int16)
            else:
                scaled = chunk
            outdata[:, 0] = scaled
            rms = float(np.sqrt(np.mean(scaled.astype(np.float32) ** 2))) / 32768.0
            self.speaker_level = min(1.0, rms * 4.5)
        except queue.Empty:
            outdata.fill(0)
            self.speaker_level *= 0.7

    def push_remote_audio(self, pcm_bytes: bytes):
        """Encola audio PCM entrante de la llamada P2P para reproducirlo en el parlante."""
        if not self.running or self.speaker_muted or not pcm_bytes:
            return
        try:
            samples = np.frombuffer(pcm_bytes, dtype=np.int16)
            if self.play_queue.full():
                try:
                    self.play_queue.get_nowait()
                except queue.Empty:
                    pass
            self.play_queue.put_nowait(samples)
        except Exception:
            pass

    def play_test_tone(self):
        """Genera un acorde limpio en el Parlante seleccionado para verificar su funcionamiento."""
        if not HAS_SOUNDDEVICE:
            return

        def _tone_worker():
            was_running = self.running
            if not was_running:
                self.start()
            sr = self.SAMPLE_RATE
            n = self.BLOCK_SAMPLES
            freqs = [523.25, 659.25, 783.99]  # Acorde Do Mayor agradable
            for f in freqs:
                for blk in range(4):
                    t = (np.arange(n, dtype=np.float32) + blk * n) / sr
                    wave = (np.sin(2.0 * np.pi * f * t) * 11000.0).astype(np.int16)
                    self.push_remote_audio(wave.tobytes())
                    time.sleep(0.035)
            if not was_running:
                time.sleep(0.3)
                self.stop()

        threading.Thread(target=_tone_worker, daemon=True).start()

    def stop(self):
        with self._lock:
            self.running = False
            for st in (self._in_stream, self._out_stream):
                if st is not None:
                    try:
                        st.stop()
                        st.close()
                    except Exception:
                        pass
            self._in_stream = None
            self._out_stream = None


class PerpetualP2PNode:
    """
    Motor de Llamada Perpetua 24/7 en Red LAN P2P:
    - Auto-descubre otros equipos ejecutando este mismo programa de Python en la red local
      mediante paquetes UDP Broadcast en el puerto 37990.
    - Cuando 'auto_join' está activo (o se conecta manualmente), establece automáticamente
      una llamada perpetua 24/7 de Video HD (TCP Zero-Lag JPEG) + Audio Bidireccional (UDP PCM).
    - Si el otro equipo se reinicia o se desconecta momentáneamente, reconecta de forma
      automática e infinita las 24 horas del día.
    """

    DISCOVERY_PORT = 37990
    VIDEO_TCP_PORT = 37991
    AUDIO_UDP_PORT = 37992
    MAGIC_HEADER = b"DCMP2P1"
    GAME_MAGIC = b"DCMP2PG"

    def __init__(self, node_name: str = ""):
        self.hostname = node_name or socket.gethostname()
        self.node_id = f"{self.hostname}_{os.getpid()}_{int(time.time()) % 10000}"
        self.local_ips = get_local_lan_ips()

        self.running = False
        self.auto_join = True
        self.target_peer_ip = ""  # Vacío = Auto-unir a cualquier equipo P2P descubierto en la red
        self.jpeg_quality = 80

        # Estado de pares descubiertos en la red LAN: {node_id: {ip, hostname, video_port, audio_port, last_seen}}
        self.discovered_peers: Dict[str, Dict] = {}
        self.peers_lock = threading.Lock()

        # Sesión de llamada activa
        self.conn_lock = threading.Lock()
        self.active_video_sock: Optional[socket.socket] = None
        self.active_peer_ip: str = ""
        self.active_peer_name: str = ""
        self.active_peer_audio_port: int = self.AUDIO_UDP_PORT
        self.in_call: bool = False
        self.call_connected_since: float = 0.0

        # Último cuadro de video remoto recibido y estado de juego Kinect P2P
        self.remote_frame_lock = threading.Lock()
        self.latest_remote_frame: Optional[np.ndarray] = None
        self.latest_remote_seq: int = 0
        self.last_remote_frame_time: float = 0.0
        self.remote_fps: float = 0.0
        self._remote_frame_counter: int = 0
        self._remote_fps_timer: float = time.time()
        self.remote_game_state: Optional[Dict] = None

        # Cola de envío de video local (máximo 1 frame para CERO latencia)
        self.outbound_video_queue: "queue.Queue[bytes]" = queue.Queue(maxsize=1)

        # Motor de audio integrado (Micrófono + Parlante)
        self.audio_engine = AudioStreamEngine(on_local_audio_chunk=self._send_audio_packet)
        self.audio_udp_sock: Optional[socket.socket] = None

    def start(self):
        if self.running:
            return
        self.running = True
        self.local_ips = get_local_lan_ips()

        # Socket UDP para audio bidireccional
        try:
            self.audio_udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.audio_udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.audio_udp_sock.bind(("0.0.0.0", self.AUDIO_UDP_PORT))
        except Exception:
            self.audio_udp_sock = None

        self.audio_engine.start()

        threading.Thread(target=self._discovery_broadcaster_loop, daemon=True).start()
        threading.Thread(target=self._discovery_listener_loop, daemon=True).start()
        threading.Thread(target=self._video_tcp_server_loop, daemon=True).start()
        threading.Thread(target=self._video_outbound_sender_loop, daemon=True).start()
        threading.Thread(target=self._audio_udp_receiver_loop, daemon=True).start()
        threading.Thread(target=self._auto_join_manager_loop, daemon=True).start()

    def stop(self):
        self.running = False
        self.disconnect_current_call()
        self.audio_engine.stop()
        if self.audio_udp_sock:
            try:
                self.audio_udp_sock.close()
            except Exception:
                pass
            self.audio_udp_sock = None

    def disconnect_current_call(self):
        with self.conn_lock:
            sock = self.active_video_sock
            self.active_video_sock = None
            self.in_call = False
            self.active_peer_ip = ""
            self.active_peer_name = ""
        if sock:
            try:
                sock.close()
            except Exception:
                pass

    def _discovery_broadcaster_loop(self):
        """Anuncia este Portal en la red LAN cada 1.5s para que otros equipos se auto-unan."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        while self.running:
            try:
                self.local_ips = get_local_lan_ips()
                payload = {
                    "magic": "DEACAM_PORTAL_P2P",
                    "node_id": self.node_id,
                    "hostname": self.hostname,
                    "ips": self.local_ips,
                    "video_port": self.VIDEO_TCP_PORT,
                    "audio_port": self.AUDIO_UDP_PORT,
                    "in_call": self.in_call,
                    "auto_join": self.auto_join,
                    "ts": time.time(),
                }
                raw = json.dumps(payload).encode("utf-8")

                targets = {"255.255.255.255"}
                for lip in self.local_ips:
                    parts = lip.split(".")
                    if len(parts) == 4 and parts[0] != "127":
                        targets.add(f"{parts[0]}.{parts[1]}.{parts[2]}.255")

                for t_ip in targets:
                    try:
                        sock.sendto(raw, (t_ip, self.DISCOVERY_PORT))
                    except Exception:
                        pass
            except Exception:
                pass
            time.sleep(1.5)

        try:
            sock.close()
        except Exception:
            pass

    def _discovery_listener_loop(self):
        """Escucha anuncios UDP de otros equipos con el Portal Python en la misma red."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.settimeout(1.5)
        try:
            sock.bind(("0.0.0.0", self.DISCOVERY_PORT))
        except Exception:
            return

        while self.running:
            try:
                data, addr = sock.recvfrom(8192)
                obj = json.loads(data.decode("utf-8", errors="ignore"))
                if obj.get("magic") != "DEACAM_PORTAL_P2P":
                    continue
                peer_id = obj.get("node_id", "")
                if not peer_id or peer_id == self.node_id:
                    continue

                peer_ip = addr[0]
                if peer_ip in self.local_ips or peer_ip.startswith("127."):
                    continue

                with self.peers_lock:
                    self.discovered_peers[peer_id] = {
                        "node_id": peer_id,
                        "ip": peer_ip,
                        "hostname": obj.get("hostname", peer_ip),
                        "video_port": int(obj.get("video_port", self.VIDEO_TCP_PORT)),
                        "audio_port": int(obj.get("audio_port", self.AUDIO_UDP_PORT)),
                        "in_call": bool(obj.get("in_call", False)),
                        "last_seen": time.time(),
                    }
            except socket.timeout:
                pass
            except Exception:
                pass

            # Limpiar pares inactivos (> 8 segundos sin señal)
            now = time.time()
            with self.peers_lock:
                stale = [k for k, v in self.discovered_peers.items() if now - v["last_seen"] > 8.0]
                for k in stale:
                    del self.discovered_peers[k]

        try:
            sock.close()
        except Exception:
            pass

    def get_peers_list(self) -> List[Dict]:
        with self.peers_lock:
            return list(self.discovered_peers.values())

    def _auto_join_manager_loop(self):
        """
        Vigila 24/7 el estado de la llamada: si 'auto_join' está habilitado y no estamos en llamada,
        se une automáticamente al equipo disponible en la red P2P o a la IP configurada.
        """
        while self.running:
            if not self.in_call and self.auto_join:
                target_ip = self.target_peer_ip.strip()
                target_port = self.VIDEO_TCP_PORT
                target_name = target_ip

                if not target_ip or target_ip.lower().startswith("auto"):
                    peers = self.get_peers_list()
                    if peers:
                        # Orden determinista para evitar colisión simultánea o conectar de inmediato
                        peers.sort(key=lambda p: p["ip"])
                        chosen = peers[0]
                        target_ip = chosen["ip"]
                        target_port = chosen["video_port"]
                        target_name = f"{chosen['hostname']} ({chosen['ip']})"

                if target_ip and target_ip not in self.local_ips:
                    self.connect_to_peer(target_ip, target_port, target_name)

            # Si estamos en llamada pero llevamos más de 6 segundos sin recibir paquetes ni latido, verificar socket
            if self.in_call and self.last_remote_frame_time > 0:
                if time.time() - self.last_remote_frame_time > 6.5:
                    self.disconnect_current_call()

            time.sleep(1.2)

    def connect_to_peer(self, peer_ip: str, video_port: int = VIDEO_TCP_PORT, peer_name: str = "") -> bool:
        """Inicia conexión saliente hacia otro Portal en la red LAN."""
        peer_ip = peer_ip.strip()
        if not peer_ip or self.in_call:
            return False
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            s.settimeout(2.5)
            s.connect((peer_ip, video_port))
            s.settimeout(4.0)

            # Enviar saludo inicial con nuestro nombre y puerto de audio
            hello = json.dumps({
                "hostname": self.hostname,
                "audio_port": self.AUDIO_UDP_PORT,
            }).encode("utf-8")
            s.sendall(self.MAGIC_HEADER + struct.pack("!BI", 1, len(hello)) + hello)

            self._adopt_connection(s, peer_ip, peer_name or peer_ip)
            return True
        except Exception:
            return False

    def _video_tcp_server_loop(self):
        """Servidor TCP 24/7 que acepta conexiones entrantes de otros Portales en la red."""
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.settimeout(1.5)
        try:
            srv.bind(("0.0.0.0", self.VIDEO_TCP_PORT))
            srv.listen(4)
        except Exception:
            return

        while self.running:
            try:
                client_sock, addr = srv.accept()
                client_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                client_sock.settimeout(4.0)
                peer_ip = addr[0]

                # Si ya estamos en llamada con otro, cerramos la vieja si estaba muerta o aceptamos la nueva
                if self.in_call and self.active_peer_ip != peer_ip:
                    if time.time() - self.last_remote_frame_time > 3.0:
                        self.disconnect_current_call()
                    else:
                        try:
                            client_sock.close()
                        except Exception:
                            pass
                        continue

                # Enviar nuestro saludo al par entrante
                hello = json.dumps({
                    "hostname": self.hostname,
                    "audio_port": self.AUDIO_UDP_PORT,
                }).encode("utf-8")
                try:
                    client_sock.sendall(self.MAGIC_HEADER + struct.pack("!BI", 1, len(hello)) + hello)
                except Exception:
                    pass

                self._adopt_connection(client_sock, peer_ip, peer_ip)
            except socket.timeout:
                pass
            except Exception:
                time.sleep(0.3)

        try:
            srv.close()
        except Exception:
            pass

    def _adopt_connection(self, sock: socket.socket, peer_ip: str, peer_name: str):
        with self.conn_lock:
            if self.active_video_sock is not None:
                try:
                    self.active_video_sock.close()
                except Exception:
                    pass
            self.active_video_sock = sock
            self.active_peer_ip = peer_ip
            self.active_peer_name = peer_name
            self.in_call = True
            self.call_connected_since = time.time()
            self.last_remote_frame_time = time.time()

        threading.Thread(
            target=self._video_inbound_receiver_loop,
            args=(sock, peer_ip),
            daemon=True,
        ).start()

    def _recv_exact(self, sock: socket.socket, n: int) -> Optional[bytes]:
        buf = bytearray()
        while len(buf) < n and self.running:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                return None
            buf.extend(chunk)
        return bytes(buf)

    def _video_inbound_receiver_loop(self, sock: socket.socket, peer_ip: str):
        """Recibe continuamente cuadros JPEG-Turbo del Portal remoto."""
        header_len = len(self.MAGIC_HEADER) + 5  # 7 bytes magic + 1 byte pkt_type + 4 bytes size
        while self.running:
            with self.conn_lock:
                if sock is not self.active_video_sock:
                    break
            try:
                hdr = self._recv_exact(sock, header_len)
                if not hdr or not hdr.startswith(self.MAGIC_HEADER):
                    break
                pkt_type, payload_len = struct.unpack("!BI", hdr[len(self.MAGIC_HEADER):])
                if payload_len <= 0 or payload_len > 4_500_000:
                    break
                payload = self._recv_exact(sock, payload_len)
                if not payload:
                    break

                self.last_remote_frame_time = time.time()

                if pkt_type == 1:
                    # Saludo JSON con metadatos del par remoto
                    try:
                        meta = json.loads(payload.decode("utf-8", errors="ignore"))
                        r_host = meta.get("hostname", peer_ip)
                        self.active_peer_name = f"{r_host} ({peer_ip})"
                        self.active_peer_audio_port = int(meta.get("audio_port", self.AUDIO_UDP_PORT))
                    except Exception:
                        pass
                elif pkt_type == 2:
                    # Cuadro de video JPEG
                    arr = np.frombuffer(payload, dtype=np.uint8)
                    frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                    if frame is not None:
                        with self.remote_frame_lock:
                            self.latest_remote_frame = frame
                            self.latest_remote_seq += 1
                        self._remote_frame_counter += 1
                        now = time.time()
                        if now - self._remote_fps_timer >= 1.0:
                            self.remote_fps = self._remote_frame_counter / (now - self._remote_fps_timer)
                            self._remote_frame_counter = 0
                            self._remote_fps_timer = now
            except Exception:
                break

        with self.conn_lock:
            if sock is self.active_video_sock:
                self.active_video_sock = None
                self.in_call = False
                self.active_peer_ip = ""
                self.active_peer_name = ""
        try:
            sock.close()
        except Exception:
            pass

    def _video_outbound_sender_loop(self):
        """Envía el último cuadro procesado local al par conectado sin bloquear el compositor."""
        while self.running:
            try:
                pkt = self.outbound_video_queue.get(timeout=0.5)
            except queue.Empty:
                # Si estamos en llamada, enviar latido ligero para mantener viva la conexión TCP
                continue

            with self.conn_lock:
                sock = self.active_video_sock
            if sock is None:
                continue

            try:
                sock.sendall(pkt)
            except Exception:
                self.disconnect_current_call()

    def send_local_video_frame(self, frame_bgr: np.ndarray):
        """
        Comprime y encola el cuadro ya procesado por el Panel Admin (con modo espejo, zoom IA,
        oscilación y blureo de señas/celulares aplicado) para transmitirlo a la llamada P2P.
        """
        if not self.running or not self.in_call or frame_bgr is None:
            return
        try:
            ok, enc = cv2.imencode(
                ".jpg",
                frame_bgr,
                [int(cv2.IMWRITE_JPEG_QUALITY), int(self.jpeg_quality)],
            )
            if not ok:
                return
            jpg_bytes = enc.tobytes()
            pkt = self.MAGIC_HEADER + struct.pack("!BI", 2, len(jpg_bytes)) + jpg_bytes
            if self.outbound_video_queue.full():
                try:
                    self.outbound_video_queue.get_nowait()
                except queue.Empty:
                    pass
            self.outbound_video_queue.put_nowait(pkt)
        except Exception:
            pass

    def send_game_state(self, state_dict: Dict):
        """Envía estado de manos/extremidades y juegos Kinect por UDP de baja latencia al Portal remoto."""
        if not self.running or not self.in_call or not self.active_peer_ip or not self.audio_udp_sock:
            return
        try:
            raw = json.dumps(state_dict).encode("utf-8")
            pkt = self.GAME_MAGIC + raw
            self.audio_udp_sock.sendto(pkt, (self.active_peer_ip, self.active_peer_audio_port))
        except Exception:
            pass

    def get_remote_game_state(self) -> Optional[Dict]:
        """Devuelve el último estado de juego y cursores de mano recibidos del otro Portal en llamada."""
        with self.remote_frame_lock:
            return self.remote_game_state

    def _send_audio_packet(self, pcm_bytes: bytes):
        """Envía paquete UDP de voz del micrófono al parlante del equipo remoto en llamada."""
        if not self.running or not self.in_call or not self.active_peer_ip or not self.audio_udp_sock:
            return
        try:
            pkt = self.MAGIC_HEADER + pcm_bytes
            self.audio_udp_sock.sendto(pkt, (self.active_peer_ip, self.active_peer_audio_port))
        except Exception:
            pass

    def _audio_udp_receiver_loop(self):
        """Recibe paquetes UDP de audio y telemetría de juegos Kinect del par remoto."""
        while self.running:
            if self.audio_udp_sock is None:
                time.sleep(0.5)
                continue
            try:
                data, addr = self.audio_udp_sock.recvfrom(8192)
                if not data:
                    continue
                if data.startswith(self.MAGIC_HEADER):
                    pcm = data[len(self.MAGIC_HEADER):]
                    self.audio_engine.push_remote_audio(pcm)
                elif data.startswith(self.GAME_MAGIC):
                    raw_json = data[len(self.GAME_MAGIC):].decode("utf-8", errors="ignore")
                    obj = json.loads(raw_json)
                    with self.remote_frame_lock:
                        self.remote_game_state = obj
            except Exception:
                time.sleep(0.05)

    def get_remote_video_frame(self) -> Tuple[Optional[np.ndarray], int, bool]:
        """Devuelve (frame_bgr, seq, is_live_in_call)."""
        with self.remote_frame_lock:
            is_fresh = self.in_call and (time.time() - self.last_remote_frame_time < 4.5)
            return self.latest_remote_frame, self.latest_remote_seq, is_fresh
