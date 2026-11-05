"""Pruebas de la version ligera. Ejecutar:  python -m unittest test_ligero -v"""
import socket
import struct
import threading
import time
import unittest

import cv2
import numpy as np

import p2p_mesh_lite
from lite_pipeline import LiteVideoPipeline
from p2p_mesh_lite import MultiPeerMeshNode


def _jpg(w, h, q=70):
    img = np.random.randint(0, 255, (h, w, 3), dtype=np.uint8)
    ok, enc = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), q])
    assert ok
    return enc.tobytes()


def _pkt(node, payload, pkt_type=2):
    return node.MAGIC_HEADER + struct.pack("!BI", pkt_type, len(payload)) + payload


class RecepcionLigera(unittest.TestCase):
    def _run_receiver(self, payloads, spacing=0.0):
        """Manda `payloads` por un socketpair al receptor y devuelve (frames_decodificados, forma_ultimo_cuadro)."""
        node = MultiPeerMeshNode(node_name="test")
        node.running = True
        a, b = socket.socketpair()
        node.active_conns["10.0.0.9"] = {"sock": b, "name": "x", "audio_port": 1, "last_frame_ts": time.time()}
        decoded = []
        real = cv2.imdecode
        cv2.imdecode = lambda arr, flag: (decoded.append(flag), real(arr, flag))[1]
        try:
            t = threading.Thread(target=node._peer_receiver_loop, args=(b, "10.0.0.9"), daemon=True)
            t.start()
            for p in payloads:
                a.sendall(_pkt(node, p))
                if spacing:
                    time.sleep(spacing)
            time.sleep(0.4)
            shape = node.peer_frames.get("10.0.0.9", {}).get("frame")
            node.running = False
            a.close()
            t.join(2)
        finally:
            cv2.imdecode = real
        return decoded, (None if shape is None else shape.shape)

    def test_descarta_cuadros_de_mas_sin_decodificar(self):
        # 30 cuadros en rafa (el otro equipo manda a ~30+ fps): solo se decodifican unos pocos
        decoded, _ = self._run_receiver([_jpg(640, 360)] * 30)
        self.assertGreaterEqual(len(decoded), 1)
        self.assertLessEqual(len(decoded), 3)

    def test_jpeg_grande_se_decodifica_a_mitad_de_tamano(self):
        decoded, shape = self._run_receiver([_jpg(1280, 720)])
        self.assertEqual(decoded, [cv2.IMREAD_REDUCED_COLOR_2])
        self.assertEqual(shape, (360, 640, 3))

    def test_jpeg_pequeno_se_decodifica_completo(self):
        decoded, shape = self._run_receiver([_jpg(854, 480)])
        self.assertEqual(decoded, [cv2.IMREAD_COLOR])
        self.assertEqual(shape, (480, 854, 3))

    def test_ancho_jpeg_leido_de_cabecera(self):
        self.assertEqual(MultiPeerMeshNode.jpeg_width(_jpg(1280, 720)), 1280)
        self.assertEqual(MultiPeerMeshNode.jpeg_width(_jpg(854, 480)), 854)
        self.assertEqual(MultiPeerMeshNode.jpeg_width(b"basura"), 0)

    def test_calidad_jpeg_ligera(self):
        self.assertEqual(MultiPeerMeshNode().jpeg_quality, 65)


class PipelineLigero(unittest.TestCase):
    def test_salida_16_9_por_defecto_854x480(self):
        pipe = LiteVideoPipeline("models")
        out, _ = pipe.process_frame(np.zeros((360, 640, 3), np.uint8))
        self.assertEqual(out.shape, (480, 854, 3))

    def test_deteccion_menos_frecuente(self):
        self.assertGreaterEqual(LiteVideoPipeline("models").detect_interval, 0.25)


class _FakeCap:
    def __init__(self, w, h):
        self.frame = np.full((h, w, 3), 90, np.uint8)

    def isOpened(self):
        return True

    def read(self):
        time.sleep(0.01)
        return True, self.frame

    def release(self):
        pass


class CamaraLigera(unittest.TestCase):
    def _grabber(self, fails_first):
        from p2p_portal import UniversalCameraGrabber

        class G(UniversalCameraGrabber):
            calls = 0

            def _open_capture(self):
                G.calls += 1
                if fails_first and G.calls == 1:
                    raise RuntimeError("fallo simulado de OpenCV")
                return _FakeCap(640, 480)  # webcam 4:3

        return G("USB:0", source_type="usb")

    def _esperar_cuadro(self, g, timeout=4.0):
        t0 = time.time()
        while time.time() - t0 < timeout:
            frame, _, ok = g.get_latest()
            if ok and frame is not None:
                return frame
            time.sleep(0.05)
        return None

    def test_recorta_a_16_9(self):
        g = self._grabber(fails_first=False)
        frame = self._esperar_cuadro(g)
        g.stop()
        self.assertIsNotNone(frame)
        self.assertEqual(frame.shape, (360, 640, 3))  # 640x480 -> 640x360

    def test_reintenta_si_falla_al_abrir(self):
        g = self._grabber(fails_first=True)
        frame = self._esperar_cuadro(g)  # el reintento ocurre ~1 s despues
        g.stop()
        self.assertIsNotNone(frame, "el hilo de la camara murio tras la excepcion")


if __name__ == "__main__":
    unittest.main()
