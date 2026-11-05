import os
import sys
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

from p2p_portal import (
    get_local_lan_ips,
    discover_usb_cameras,
    UniversalCameraGrabber,
    AudioStreamEngine,
)


class MultiPeerMeshNode:
    """
    Motor P2P Mesh 24/7 para Portal Lite:
    - Auto-descubre en la misma red LAN por UDP Broadcast (puerto 37990).
    - Permite llamada perpetua 24/7 de hasta 3 PARTICIPANTES SIMULTÁNEOS.
    - Cada participante envía su cuadro de video con suavizado IA y su audio de micrófono.
    - Si hay 1 participante remoto conectado: Vista 1 a 1 de pantalla completa.
    - Si hay 2 participantes remotos conectados: Vista Dividida cinematográfica (o cuadrícula armónica).
    - Audio mezclado automáticamente de todos los participantes conectados en el parlante.
    """

    DISCOVERY_PORT = 37990
    VIDEO_TCP_PORT = 37991
    AUDIO_UDP_PORT = 37992
    MAGIC_HEADER = b"DCMLITE"
    MAX_PEERS = 2  # Nosotros (1) + hasta 2 remotos = 3 participantes máximo

    def __init__(self, node_name: str = ""):
        self.hostname = node_name or socket.gethostname()
        self.node_id = f"{self.hostname}_{os.getpid()}_{int(time.time()) % 10000}"
        self.local_ips = get_local_lan_ips()

        self.running = False
        self.auto_join = True
        self.jpeg_quality = 65  # VERSION LIGERA (la original usa 78)
        # VERSION LIGERA: máximo de cuadros por segundo que se decodifican por participante remoto
        # (el resto se descarta sin decodificar; el otro equipo puede mandar a 30-40 fps).
        self.max_decode_fps = 12.0
        # Si el JPEG recibido mide al menos este ancho, se decodifica a mitad de tamaño (mucho más rápido).
        self.reduce_decode_min_width = 1100

        # Pares descubiertos en LAN {node_id: {ip, hostname, video_port, audio_port, last_seen}}
        self.peers_lock = threading.Lock()
        self.discovered_peers: Dict[str, Dict] = {}

        # Conexiones remotas activas {peer_ip: {"sock": sock, "name": name, "audio_port": port, "last_frame_ts": float}}
        self.conns_lock = threading.Lock()
        self.active_conns: Dict[str, Dict] = {}

        # Últimos cuadros recibidos de cada participante {peer_ip: {"frame": np.ndarray, "seq": int, "name": str, "fps": float}}
        self.frames_lock = threading.Lock()
        self.peer_frames: Dict[str, Dict] = {}

        # Cola de salida de video local hacia cada peer
        self.outbound_queue: "queue.Queue[bytes]" = queue.Queue(maxsize=2)

        # Motor de audio con mezcla continua
        self.audio_engine = AudioStreamEngine(on_local_audio_chunk=self._broadcast_audio_chunk)
        self.audio_udp_sock: Optional[socket.socket] = None

        # Lista de IPs destino manuales (para cuando los switches corporativos bloquean UDP Broadcast)
        self.manual_target_ips: List[str] = []

    def set_target_peers(self, ips: List[str]):
        clean = []
        for ip in ips:
            s = str(ip).strip()
            if s and not s.startswith("Auto") and s not in self.local_ips and not s.startswith("127."):
                if s not in clean:
                    clean.append(s)
        self.manual_target_ips = clean

    def start(self):
        if self.running:
            return
        self.running = True
        self.local_ips = get_local_lan_ips()

        # Socket UDP para audio full-duplex y telemetría
        try:
            self.audio_udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.audio_udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.audio_udp_sock.bind(("0.0.0.0", self.AUDIO_UDP_PORT))
        except Exception:
            self.audio_udp_sock = None

        self.audio_engine.start()

        threading.Thread(target=self._discovery_broadcaster, daemon=True).start()
        threading.Thread(target=self._discovery_listener, daemon=True).start()
        threading.Thread(target=self._video_server_loop, daemon=True).start()
        threading.Thread(target=self._video_outbound_loop, daemon=True).start()
        threading.Thread(target=self._audio_inbound_loop, daemon=True).start()
        threading.Thread(target=self._mesh_auto_join_loop, daemon=True).start()

    def stop(self):
        self.running = False
        self.audio_engine.stop()
        with self.conns_lock:
            for ip, c in list(self.active_conns.items()):
                try:
                    c["sock"].close()
                except Exception:
                    pass
            self.active_conns.clear()

        if self.audio_udp_sock:
            try:
                self.audio_udp_sock.close()
            except Exception:
                pass
            self.audio_udp_sock = None

    def _discovery_broadcaster(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        while self.running:
            try:
                self.local_ips = get_local_lan_ips()
                with self.conns_lock:
                    in_call_count = len(self.active_conns)
                payload = {
                    "magic": "DEACAM_LITE_P2P",
                    "node_id": self.node_id,
                    "hostname": self.hostname,
                    "ips": self.local_ips,
                    "video_port": self.VIDEO_TCP_PORT,
                    "audio_port": self.AUDIO_UDP_PORT,
                    "in_call_count": in_call_count,
                    "auto_join": self.auto_join,
                    "ts": time.time(),
                }
                raw = json.dumps(payload).encode("utf-8")

                targets = {"255.255.255.255"}
                for lip in self.local_ips:
                    parts = lip.split(".")
                    if len(parts) == 4 and parts[0] != "127":
                        targets.add(f"{parts[0]}.{parts[1]}.{parts[2]}.255")
                for m_ip in list(self.manual_target_ips):
                    if m_ip:
                        targets.add(m_ip)

                for t_ip in targets:
                    try:
                        sock.sendto(raw, (t_ip, self.DISCOVERY_PORT))
                    except Exception:
                        pass
            except Exception:
                pass
            time.sleep(1.4)

        try:
            sock.close()
        except Exception:
            pass

    def _discovery_listener(self):
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
                # Compatible con anuncios Lite y anuncios Portal general
                if obj.get("magic") not in ("DEACAM_LITE_P2P", "DEACAM_PORTAL_P2P"):
                    continue
                peer_id = obj.get("node_id", "")
                if not peer_id or peer_id == self.node_id:
                    continue

                peer_ip = addr[0]
                if peer_ip in self.local_ips or peer_ip.startswith("127."):
                    continue

                with self.peers_lock:
                    self.discovered_peers[peer_ip] = {
                        "node_id": peer_id,
                        "ip": peer_ip,
                        "hostname": obj.get("hostname", peer_ip),
                        "video_port": int(obj.get("video_port", self.VIDEO_TCP_PORT)),
                        "audio_port": int(obj.get("audio_port", self.AUDIO_UDP_PORT)),
                        "last_seen": time.time(),
                    }
            except socket.timeout:
                pass
            except Exception:
                pass

            now = time.time()
            with self.peers_lock:
                stale = [ip for ip, v in self.discovered_peers.items() if now - v["last_seen"] > 7.0]
                for ip in stale:
                    del self.discovered_peers[ip]

        try:
            sock.close()
        except Exception:
            pass

    def _mesh_auto_join_loop(self):
        """Mantiene enlazados a todos los miembros de la red hasta un máximo de 3 participantes (nosotros + 2)."""
        while self.running:
            # 1. Conexión continua a las IPs manuales fijas (por si los switches corporativos bloquean Broadcast UDP)
            for m_ip in list(self.manual_target_ips):
                if not m_ip or m_ip in self.local_ips or m_ip.startswith("127."):
                    continue
                with self.conns_lock:
                    already = m_ip in self.active_conns
                    now_count = len(self.active_conns)
                if not already and now_count < self.MAX_PEERS:
                    self.connect_to_peer(m_ip, self.VIDEO_TCP_PORT, f"Participante ({m_ip})")
                    time.sleep(0.3)

            # 2. Conexión a pares descubiertos dinámicamente por Broadcast LAN
            if self.auto_join:
                with self.conns_lock:
                    current_count = len(self.active_conns)
                if current_count < self.MAX_PEERS:
                    with self.peers_lock:
                        candidates = list(self.discovered_peers.values())
                    # Ordenar por IP para conexión simétrica estable
                    candidates.sort(key=lambda p: p["ip"])
                    for cand in candidates:
                        p_ip = cand["ip"]
                        p_node_id = cand.get("node_id", "")
                        with self.conns_lock:
                            already = p_ip in self.active_conns
                            now_count = len(self.active_conns)
                        if not already and now_count < self.MAX_PEERS:
                            # Prioridad determinista: conecta el nodo con mayor ID para evitar colisiones
                            if self.node_id > p_node_id or (time.time() - cand.get("last_seen", 0) > 4.0):
                                self.connect_to_peer(p_ip, cand["video_port"], cand["hostname"])
                                time.sleep(0.3)

            # Comprobar latido y desconectar peers muertos (> 5.5s sin recibir video)
            now = time.time()
            to_remove = []
            with self.conns_lock:
                for ip, c in self.active_conns.items():
                    if now - c.get("last_frame_ts", now) > 5.5:
                        to_remove.append(ip)

            for ip in to_remove:
                self.disconnect_peer(ip)

            time.sleep(1.2)

    def connect_to_peer(self, peer_ip: str, video_port: int = VIDEO_TCP_PORT, peer_name: str = "") -> bool:
        peer_ip = peer_ip.strip()
        if not peer_ip or peer_ip in self.local_ips:
            return False
        with self.conns_lock:
            if peer_ip in self.active_conns or len(self.active_conns) >= self.MAX_PEERS:
                return False

        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            s.settimeout(2.5)
            s.connect((peer_ip, video_port))
            s.settimeout(4.0)

            hello = json.dumps({
                "hostname": self.hostname,
                "audio_port": self.AUDIO_UDP_PORT,
                "node_id": self.node_id,
            }).encode("utf-8")
            s.sendall(self.MAGIC_HEADER + struct.pack("!BI", 1, len(hello)) + hello)

            self._register_connection(s, peer_ip, peer_name or peer_ip)
            return True
        except Exception:
            return False

    def _video_server_loop(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.settimeout(1.5)
        try:
            srv.bind(("0.0.0.0", self.VIDEO_TCP_PORT))
            srv.listen(6)
        except Exception:
            return

        while self.running:
            try:
                client_sock, addr = srv.accept()
                client_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                client_sock.settimeout(4.0)
                peer_ip = addr[0]

                with self.conns_lock:
                    if len(self.active_conns) >= self.MAX_PEERS and peer_ip not in self.active_conns:
                        # Ya alcanzamos el cupo de 3 participantes (nosotros + 2)
                        client_sock.close()
                        continue
                    existing = self.active_conns.get(peer_ip)
                    if existing and existing.get("sock") and (time.time() - existing.get("last_frame_ts", 0) < 3.0):
                        # Conexión existente aún activa y saludable, descartar conexión redundante
                        client_sock.close()
                        continue

                hello = json.dumps({
                    "hostname": self.hostname,
                    "audio_port": self.AUDIO_UDP_PORT,
                    "node_id": self.node_id,
                }).encode("utf-8")
                try:
                    client_sock.sendall(self.MAGIC_HEADER + struct.pack("!BI", 1, len(hello)) + hello)
                except Exception:
                    pass

                self._register_connection(client_sock, peer_ip, peer_ip)
            except socket.timeout:
                pass
            except Exception:
                time.sleep(0.3)

        try:
            srv.close()
        except Exception:
            pass

    def _register_connection(self, sock: socket.socket, peer_ip: str, peer_name: str):
        with self.conns_lock:
            old = self.active_conns.get(peer_ip)
            if old and old.get("sock"):
                try:
                    old["sock"].close()
                except Exception:
                    pass
            self.active_conns[peer_ip] = {
                "sock": sock,
                "name": peer_name,
                "audio_port": self.AUDIO_UDP_PORT,
                "connected_since": time.time(),
                "last_frame_ts": time.time(),
            }

        threading.Thread(
            target=self._peer_receiver_loop,
            args=(sock, peer_ip),
            daemon=True,
        ).start()

    def disconnect_peer(self, peer_ip: str, sock: Optional[socket.socket] = None):
        to_close = None
        with self.conns_lock:
            c = self.active_conns.get(peer_ip)
            if c:
                if sock is None or c.get("sock") is sock:
                    self.active_conns.pop(peer_ip, None)
                    to_close = c.get("sock")
        if to_close:
            try:
                to_close.close()
            except Exception:
                pass
        with self.frames_lock:
            self.peer_frames.pop(peer_ip, None)

    @staticmethod
    def jpeg_width(data: bytes) -> int:
        """Ancho de un JPEG leyendo su cabecera (SOF), sin decodificarlo. 0 si no se puede leer."""
        i, n = 2, len(data)
        while i + 9 < n:
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker == 0xFF:
                i += 1
                continue
            if marker in (0xC0, 0xC1, 0xC2):
                return (data[i + 7] << 8) | data[i + 8]
            if marker == 0xD8 or 0xD0 <= marker <= 0xD7 or marker == 0x01:
                i += 2
                continue
            i += 2 + ((data[i + 2] << 8) | data[i + 3])
        return 0

    def _recv_exact(self, sock: socket.socket, n: int) -> Optional[bytes]:
        buf = bytearray()
        while len(buf) < n and self.running:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                return None
            buf.extend(chunk)
        return bytes(buf)

    def _peer_receiver_loop(self, sock: socket.socket, peer_ip: str):
        hdr_len = len(self.MAGIC_HEADER) + 5
        fps_counter = 0
        fps_timer = time.time()
        calc_fps = 0.0
        last_decode_ts = 0.0

        while self.running:
            with self.conns_lock:
                c = self.active_conns.get(peer_ip)
                if not c or c["sock"] is not sock:
                    break

            try:
                hdr = self._recv_exact(sock, hdr_len)
                if not hdr or not (hdr.startswith(self.MAGIC_HEADER) or hdr.startswith(b"DCMP2P1")):
                    break
                # Extraer pkt_type y payload_len
                magic_offset = len(self.MAGIC_HEADER) if hdr.startswith(self.MAGIC_HEADER) else 7
                pkt_type, payload_len = struct.unpack("!BI", hdr[magic_offset:])
                if payload_len <= 0 or payload_len > 4_500_000:
                    break
                payload = self._recv_exact(sock, payload_len)
                if not payload:
                    break

                now = time.time()
                with self.conns_lock:
                    if peer_ip in self.active_conns:
                        self.active_conns[peer_ip]["last_frame_ts"] = now

                if pkt_type == 1:
                    # Metadatos del par remoto
                    try:
                        meta = json.loads(payload.decode("utf-8", errors="ignore"))
                        r_name = meta.get("hostname", peer_ip)
                        with self.conns_lock:
                            if peer_ip in self.active_conns:
                                self.active_conns[peer_ip]["name"] = f"{r_name} ({peer_ip})"
                                self.active_conns[peer_ip]["audio_port"] = int(meta.get("audio_port", self.AUDIO_UDP_PORT))
                    except Exception:
                        pass

                elif pkt_type == 2:
                    # Cuadro de video JPEG (VERSION LIGERA: se descartan los de más y se decodifica reducido)
                    if now - last_decode_ts < 1.0 / self.max_decode_fps:
                        continue
                    last_decode_ts = now
                    arr = np.frombuffer(payload, dtype=np.uint8)
                    flag = cv2.IMREAD_COLOR
                    if self.jpeg_width(payload) >= self.reduce_decode_min_width:
                        flag = cv2.IMREAD_REDUCED_COLOR_2
                    frame = cv2.imdecode(arr, flag)
                    if frame is not None:
                        fps_counter += 1
                        if now - fps_timer >= 1.0:
                            calc_fps = fps_counter / (now - fps_timer)
                            fps_counter = 0
                            fps_timer = now

                        with self.conns_lock:
                            peer_label = self.active_conns.get(peer_ip, {}).get("name", peer_ip)

                        with self.frames_lock:
                            prev = self.peer_frames.get(peer_ip, {"seq": 0})
                            self.peer_frames[peer_ip] = {
                                "frame": frame,
                                "seq": prev["seq"] + 1,
                                "name": peer_label,
                                "fps": calc_fps,
                                "last_seen": now,
                            }
            except Exception:
                break

        self.disconnect_peer(peer_ip, sock=sock)

    def _video_outbound_loop(self):
        """Difunde el cuadro local hacia todos los participantes conectados en llamada."""
        while self.running:
            try:
                pkt = self.outbound_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            with self.conns_lock:
                socks = [c["sock"] for c in self.active_conns.values() if c.get("sock")]

            for s in socks:
                try:
                    s.sendall(pkt)
                except Exception:
                    pass

    def send_local_video_frame(self, frame_bgr: np.ndarray):
        if not self.running or frame_bgr is None:
            return
        with self.conns_lock:
            if not self.active_conns:
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
            if self.outbound_queue.full():
                try:
                    self.outbound_queue.get_nowait()
                except queue.Empty:
                    pass
            self.outbound_queue.put_nowait(pkt)
        except Exception:
            pass

    def _broadcast_audio_chunk(self, pcm_bytes: bytes):
        if not self.running or not self.audio_udp_sock or not pcm_bytes:
            return
        with self.conns_lock:
            targets = [(ip, c.get("audio_port", self.AUDIO_UDP_PORT)) for ip, c in self.active_conns.items()]

        pkt = self.MAGIC_HEADER + pcm_bytes
        for ip, port in targets:
            try:
                self.audio_udp_sock.sendto(pkt, (ip, port))
            except Exception:
                pass

    def _audio_inbound_loop(self):
        while self.running:
            if self.audio_udp_sock is None:
                time.sleep(0.5)
                continue
            try:
                data, addr = self.audio_udp_sock.recvfrom(4096)
                if not data:
                    continue
                if data.startswith(self.MAGIC_HEADER):
                    pcm = data[len(self.MAGIC_HEADER):]
                    self.audio_engine.push_remote_audio(pcm)
                elif data.startswith(b"DCMP2P1"):
                    pcm = data[7:]
                    self.audio_engine.push_remote_audio(pcm)
            except Exception:
                time.sleep(0.04)

    def get_remote_streams(self) -> List[Dict]:
        """Devuelve la lista de participantes remotos activos: [{'ip', 'name', 'frame', 'fps'}]."""
        now = time.time()
        streams = []
        with self.frames_lock:
            for ip, d in self.peer_frames.items():
                if now - d.get("last_seen", 0) < 4.5 and d.get("frame") is not None:
                    streams.append({
                        "ip": ip,
                        "name": d.get("name", ip),
                        "frame": d["frame"],
                        "fps": d.get("fps", 0.0),
                    })
        return streams

    def get_status_summary(self) -> Tuple[int, str]:
        with self.conns_lock:
            count = len(self.active_conns)
            names = [c.get("name", ip) for ip, c in self.active_conns.items()]

        if count == 0:
            with self.peers_lock:
                lan_count = len(self.discovered_peers)
            if lan_count > 0:
                return 0, f"🟡 En espera ({lan_count} equipos descubiertos en LAN — Conectando...)"
            lip = self.local_ips[0] if self.local_ips else "LAN"
            return 0, f"🌐 P2P Mesh 24/7 Listo (Mi IP: {lip}) — Esperando participantes..."
        elif count == 1:
            return 1, f"🟢 LLAMADA 1 A 1 CON: {names[0]}"
        else:
            total = count + 1
            return count, f"🟢 LLAMADA GRUPAL 24/7 ({total}/3 PARTICIPANTES): {', '.join(names)}"
