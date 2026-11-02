"""Eingebauter VNC-Client: Anmeldung, Kodierungen, Eingabe und Fehlerfälle gegen einen Fake-Server."""
import os
import socket
import struct
import sys
import threading
import time
import unittest
import zlib

sys.path.insert(0, os.path.dirname(__file__))

from sesam import rfb

PIXEL_FORMAT = struct.pack(">BBBBHHHBBB3x", 32, 24, 0, 1, 255, 255, 255, 16, 8, 0)


def px(r, g, b):
    """Pixel im Format des Clients (BGRX)."""
    return bytes([b, g, r, 0])


def cpx(r, g, b):
    """CPIXEL von ZRLE (3 Byte, BGR)."""
    return bytes([b, g, r])


def rect(x, y, w, h, enc, data=b""):
    return struct.pack(">HHHHi", x, y, w, h, enc) + data


def update(*rects):
    return struct.pack(">BxH", 0, len(rects)) + b"".join(rects)


class FakeServer:
    """Ein einziger Client; zeichnet dessen Nachrichten auf, sendet auf Anforderung beliebige Bytes."""

    CHALLENGE = bytes(range(16))

    def __init__(self, width=100, height=80, proto=8, security=(1,), password="geheim", reject=None, banner=None, name="Testserver"):
        self.width, self.height, self.proto, self.security = width, height, proto, tuple(security)
        self.password, self.reject, self.banner, self.name = password, reject, banner, name
        self.received = []
        self.cond = threading.Condition()
        self.conn = None
        self.send_lock = threading.Lock()
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def _recv(self, n):
        data = b""
        while len(data) < n:
            chunk = self.conn.recv(n - len(data))
            if not chunk:
                raise EOFError
            data += chunk
        return data

    def push(self, data):
        with self.send_lock:
            self.conn.sendall(data)

    def _result(self, ok):
        if ok:
            self.push(struct.pack(">I", 0))
        elif self.proto == 8:
            reason = b"zu viele Versuche"
            self.push(struct.pack(">II", 1, len(reason)) + reason)
        else:
            self.push(struct.pack(">I", 1))

    def _record(self, *entry):
        with self.cond:
            self.received.append(entry)
            self.cond.notify_all()

    def wait_for(self, kind, count=1, timeout=5.0):
        with self.cond:
            return self.cond.wait_for(lambda: sum(1 for e in self.received if e[0] == kind) >= count, timeout)

    def of(self, kind):
        with self.cond:
            return [e[1:] for e in self.received if e[0] == kind]

    def _serve(self):
        """Nimmt nacheinander Verbindungen an (z. B. nach einer Passwort-Rückfrage)."""
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            self.conn = conn
            try:
                self._handle()
            except (OSError, EOFError):
                pass
            finally:
                conn.close()

    def _handle(self):
        self.push(self.banner or b"RFB 003.00%d\n" % self.proto)
        self._recv(12)
        if self.reject is not None:
            text = self.reject.encode()
            self.push((struct.pack(">I", 0) if self.proto == 3 else b"\x00") + struct.pack(">I", len(text)) + text)
            return
        if self.proto == 3:
            chosen = self.security[0]
            self.push(struct.pack(">I", chosen))
        else:
            self.push(bytes([len(self.security)]) + bytes(self.security))
            chosen = self._recv(1)[0]
        if chosen == 2:
            self.push(self.CHALLENGE)
            ok = self._recv(16) == rfb.vnc_response(self.password, self.CHALLENGE)
            self._result(ok)
            if not ok:
                return
        elif self.proto == 8:
            self._result(True)
        self._recv(1)
        name = self.name.encode()
        self.push(struct.pack(">HH16sI", self.width, self.height, PIXEL_FORMAT, len(name)) + name)
        while True:
            kind = self._recv(1)[0]
            if kind == 0:
                self._record("pixel_format", self._recv(19)[3:])
            elif kind == 2:
                (n,) = struct.unpack(">xH", self._recv(3))
                self._record("encodings", struct.unpack(">%di" % n, self._recv(4 * n)))
            elif kind == 3:
                self._record("update_request", *struct.unpack(">BHHHH", self._recv(9)))
            elif kind == 4:
                self._record("key", *struct.unpack(">BxxI", self._recv(7)))
            elif kind == 5:
                self._record("pointer", *struct.unpack(">BHH", self._recv(5)))
            elif kind == 6:
                (n,) = struct.unpack(">xxxI", self._recv(7))
                self._record("cut_text", self._recv(n).decode("latin-1"))

    def drop(self):
        if self.conn:
            try:
                self.conn.shutdown(socket.SHUT_RDWR)  # weckt auch den Thread, der gerade in recv wartet
            except OSError:
                pass
            self.conn.close()

    def close(self):
        for s in (self.conn, self.sock):
            if s:
                try:
                    s.close()
                except OSError:
                    pass


class Recorder(rfb.Handler):
    def __init__(self):
        self.events = []
        self.cond = threading.Condition()

    def _add(self, *event):
        with self.cond:
            self.events.append(event)
            self.cond.notify_all()

    def update(self, rects):
        self._add("update", rects)

    def resize(self, width, height, fb):
        self._add("resize", width, height)

    def cursor(self, width, height, hot_x, hot_y, rgba):
        self._add("cursor", width, height, hot_x, hot_y, rgba)

    def bell(self):
        self._add("bell")

    def clipboard(self, text):
        self._add("clipboard", text)

    def wait(self, count, timeout=5.0):
        with self.cond:
            return self.cond.wait_for(lambda: len(self.events) >= count, timeout)


class Session:
    """Server + verbundener Client, dessen Empfangsschleife in einem Thread läuft."""

    def __init__(self, password=None, encodings=None, **server_args):
        self.server = FakeServer(**server_args)
        kwargs = {"encodings": encodings} if encodings else {}
        self.client = rfb.Client("127.0.0.1", self.server.port, password, timeout=5, **kwargs)
        self.handler = Recorder()
        self.error = None
        self.done = threading.Event()

    def __enter__(self):
        self.client.connect()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        return self

    def _run(self):
        try:
            self.client.run(self.handler)
        except rfb.RfbError as exc:
            self.error = exc
        finally:
            self.done.set()

    def __exit__(self, *exc):
        self.client.close()
        self.server.close()
        self.thread.join(5)

    def rgb(self, x, y):
        o = (y * self.client.width + x) * 4
        b, g, r = self.client.fb[o:o + 3]
        return (r, g, b)

    def push(self, data, events=1):
        before = len(self.handler.events)
        self.server.push(data)
        self.test_wait(before + events)

    def test_wait(self, n):
        assert self.handler.wait(n), "Ereignis kam nicht an"


class CryptoTests(unittest.TestCase):
    def test_des_known_vector(self):
        # Klassischer DES-Testvektor: Schlüssel 0123456789ABCDEF, Klartext „Now is t“ → 3FA40E8A984D4815.
        # Das Passwort enthält die in jedem Byte gespiegelten Schlüsselbytes.
        key = bytes.fromhex("0123456789ABCDEF")
        password = bytes(int(f"{b:08b}"[::-1], 2) for b in key).decode("latin-1")
        challenge = bytes.fromhex("4E6F772069732074") + bytes(8)
        response = rfb.vnc_response(password, challenge)
        self.assertEqual(response[:8].hex().upper(), "3FA40E8A984D4815")
        self.assertEqual(len(response), 16)

    def test_password_is_truncated_to_eight_bytes(self):
        challenge = bytes(range(16))
        self.assertEqual(rfb.vnc_response("12345678", challenge), rfb.vnc_response("12345678abc", challenge))


class HandshakeTests(unittest.TestCase):
    def test_versions_and_no_authentication(self):
        for proto in (3, 7, 8):
            with self.subTest(proto=proto), Session(proto=proto, security=(1,), name="Büro-PC") as s:
                self.assertEqual((s.client.width, s.client.height, s.client.name), (100, 80, "Büro-PC"))
                self.assertEqual(s.client.version, (3, proto))

    def test_client_announces_format_encodings_and_asks_for_the_screen(self):
        with Session() as s:
            self.assertTrue(s.server.wait_for("update_request"))
            self.assertEqual(s.server.of("pixel_format")[0][0], PIXEL_FORMAT)
            self.assertEqual(s.server.of("encodings")[0][0], rfb.DEFAULT_ENCODINGS)
            self.assertEqual(s.server.of("update_request")[0], (0, 0, 0, 100, 80))  # nicht inkrementell, ganzer Bildschirm

    def test_vnc_authentication(self):
        for proto in (3, 7, 8):
            with self.subTest(proto=proto), Session(password="geheim", proto=proto, security=(2,)) as s:
                pass
            with self.subTest(proto=proto, mode="mit Passwort, Server bietet beides"):
                with Session(password="geheim", proto=7 if proto == 3 else proto, security=(1, 2)) as s:
                    self.assertEqual(s.client.width, 100)

    def test_wrong_password(self):
        for proto in (3, 7, 8):
            with self.subTest(proto=proto):
                server = FakeServer(proto=proto, security=(2,), password="richtig")
                client = rfb.Client("127.0.0.1", server.port, "falsch", timeout=5)
                with self.assertRaises(rfb.AuthError) as ctx:
                    client.connect()
                if proto == 8:
                    self.assertIn("zu viele Versuche", str(ctx.exception))
                server.close()

    def test_password_required(self):
        for proto in (3, 8):
            with self.subTest(proto=proto):
                server = FakeServer(proto=proto, security=(2,))
                with self.assertRaises(rfb.PasswordRequired):
                    rfb.Client("127.0.0.1", server.port, None, timeout=5).connect()
                server.close()

    def test_unsupported_security_types(self):
        for proto in (3, 8):
            with self.subTest(proto=proto):
                server = FakeServer(proto=proto, security=(16, 19))
                with self.assertRaises(rfb.UnsupportedError):
                    rfb.Client("127.0.0.1", server.port, "x", timeout=5).connect()
                server.close()

    def test_server_rejects_the_connection(self):
        for proto in (3, 8):
            with self.subTest(proto=proto):
                server = FakeServer(proto=proto, reject="Zu viele Verbindungen")
                with self.assertRaises(rfb.RfbError) as ctx:
                    rfb.Client("127.0.0.1", server.port, timeout=5).connect()
                self.assertIn("Zu viele Verbindungen", str(ctx.exception))
                server.close()

    def test_not_a_vnc_server(self):
        server = FakeServer(banner=b"SSH-2.0-test\r\n")
        with self.assertRaises(rfb.RfbError):
            rfb.Client("127.0.0.1", server.port, timeout=5).connect()
        server.close()

    def test_connection_refused(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        with self.assertRaises(rfb.RfbError):
            rfb.Client("127.0.0.1", port, timeout=2).connect()

    def test_absurd_screen_size_is_refused(self):
        server = FakeServer(width=60000, height=60000)
        with self.assertRaises(rfb.RfbError):
            rfb.Client("127.0.0.1", server.port, timeout=5).connect()
        server.close()


class EncodingTests(unittest.TestCase):
    def test_raw_partial_and_full_width(self):
        with Session() as s:
            data = px(1, 2, 3) + px(4, 5, 6) + px(7, 8, 9) + px(10, 11, 12)
            s.push(update(rect(3, 2, 2, 2, rfb.RAW, data)))
            self.assertEqual([s.rgb(3, 2), s.rgb(4, 2), s.rgb(3, 3), s.rgb(4, 3)], [(1, 2, 3), (4, 5, 6), (7, 8, 9), (10, 11, 12)])
            self.assertEqual(s.rgb(0, 0), (0, 0, 0))
            self.assertEqual(s.handler.events[-1], ("update", [(3, 2, 2, 2)]))
            s.push(update(rect(0, 5, 100, 2, rfb.RAW, px(9, 9, 9) * 200)))
            self.assertEqual((s.rgb(0, 5), s.rgb(99, 6), s.rgb(50, 7)), ((9, 9, 9), (9, 9, 9), (0, 0, 0)))

    def test_client_requests_incremental_updates_afterwards(self):
        with Session() as s:
            s.push(update(rect(0, 0, 1, 1, rfb.RAW, px(1, 1, 1))))
            self.assertTrue(s.server.wait_for("update_request", 2))
            self.assertEqual(s.server.of("update_request")[1], (1, 0, 0, 100, 80))

    def test_copyrect_with_overlap(self):
        with Session(width=8, height=4) as s:
            image = [[(x * 10, y * 10, 7) for x in range(8)] for y in range(4)]
            s.push(update(rect(0, 0, 8, 4, rfb.RAW, b"".join(px(*image[y][x]) for y in range(4) for x in range(8)))))
            s.push(update(rect(2, 1, 4, 2, rfb.COPYRECT, struct.pack(">HH", 0, 0))))
            expected = [row[:] for row in image]
            for r in range(2):
                for c in range(4):
                    expected[1 + r][2 + c] = image[r][c]
            self.assertEqual([[s.rgb(x, y) for x in range(8)] for y in range(4)], expected)

    def test_hextile(self):
        blue, red, green, white, black = (0, 0, 255), (255, 0, 0), (0, 255, 0), (255, 255, 255), (0, 0, 0)
        tile1 = bytes([2 | 4 | 8]) + px(*blue) + px(*red) + bytes([2]) + bytes([0x00, 0x33, 0x55, 0x01])  # 4×4 bei (0,0) und 1×2 bei (5,5)
        tile2 = bytes([8 | 16, 1]) + px(*green) + bytes([0x11, 0x11])  # Hintergrund bleibt, 2×2 bei (17,1)
        raw = px(*black) + px(*white) * 63
        tile3 = bytes([1]) + raw
        tile4 = bytes([0])  # nur Hintergrund (blau)
        with Session(encodings=(rfb.HEXTILE, rfb.RAW)) as s:
            s.push(update(rect(0, 0, 20, 20, rfb.HEXTILE, tile1 + tile2 + tile3 + tile4)))
            self.assertEqual((s.rgb(0, 0), s.rgb(3, 3), s.rgb(4, 4), s.rgb(5, 5), s.rgb(5, 6), s.rgb(5, 7)), (red, red, blue, red, red, blue))
            self.assertEqual((s.rgb(16, 0), s.rgb(17, 1), s.rgb(18, 2), s.rgb(19, 3)), (blue, green, green, blue))
            self.assertEqual((s.rgb(0, 16), s.rgb(1, 16), s.rgb(15, 19)), (black, white, white))
            self.assertEqual((s.rgb(16, 16), s.rgb(19, 19)), (blue, blue))

    def test_zrle_all_tile_types(self):
        red, green, blue, yellow, cyan = (255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0), (0, 255, 255)
        comp = zlib.compressobj()  # ein Datenstrom für die ganze Verbindung

        def block(data):
            c = comp.compress(data) + comp.flush(zlib.Z_SYNC_FLUSH)
            return struct.pack(">I", len(c)) + c

        a = bytes([1]) + cpx(*red)  # 64×64 einfarbig
        b = bytes([2]) + cpx(*green) + cpx(*blue) + bytes([0b10101000]) * 64  # 6×64, 1 Bit je Pixel: 1,0,1,0,1,0
        c = bytes([128]) + cpx(*yellow) + bytes([199]) + cpx(*cyan) + bytes([183])  # 64×6 = 200 gelbe + 184 türkise Pixel
        d = bytes([131]) + cpx(*red) + cpx(*green) + cpx(*blue) + bytes([0x81, 29]) + bytes([0, 2, 0, 2, 0, 2])  # 6×6: 30× grün, dann rot/blau abwechselnd
        with Session() as s:
            s.push(update(rect(10, 5, 70, 70, rfb.ZRLE, block(a + b + c + d))))
            self.assertEqual((s.rgb(10, 5), s.rgb(73, 68), s.rgb(40, 40)), (red, red, red))
            self.assertEqual([s.rgb(74 + i, 5) for i in range(6)], [blue, green, blue, green, blue, green])
            self.assertEqual((s.rgb(74, 68), s.rgb(79, 68)), (blue, green))
            self.assertEqual((s.rgb(10, 69), s.rgb(10 + 199 % 64, 69 + 199 // 64)), (yellow, yellow))
            self.assertEqual((s.rgb(10 + 200 % 64, 69 + 200 // 64), s.rgb(73, 74)), (cyan, cyan))
            self.assertEqual((s.rgb(74, 69), s.rgb(79, 73)), (green, green))  # die ersten 30 Pixel grün
            self.assertEqual([s.rgb(74 + i, 74) for i in range(6)], [red, blue, red, blue, red, blue])

            raw_tile = bytes([0]) + b"".join(cpx(i, i, i) for i in range(1, 9))  # 4×2 unkomprimiert
            two_bit = bytes([3]) + cpx(*red) + cpx(*green) + cpx(*blue) + bytes([0x19, 0xA0])  # 4×2, 2 Bit je Pixel
            four_bit = bytes([5]) + b"".join(cpx(i * 10, 0, 0) for i in range(5)) + bytes([0x40, 0x30])  # 3×1, 4 Bit je Pixel: 4,0,3
            s.push(update(rect(0, 0, 4, 2, rfb.ZRLE, block(raw_tile)), rect(0, 10, 4, 2, rfb.ZRLE, block(two_bit)), rect(0, 20, 3, 1, rfb.ZRLE, block(four_bit))))
            self.assertEqual([s.rgb(x, 0) for x in range(4)], [(1, 1, 1), (2, 2, 2), (3, 3, 3), (4, 4, 4)])
            self.assertEqual([s.rgb(x, 1) for x in range(4)], [(5, 5, 5), (6, 6, 6), (7, 7, 7), (8, 8, 8)])
            self.assertEqual([s.rgb(x, 10) for x in range(4)], [red, green, blue, green])
            self.assertEqual([s.rgb(x, 11) for x in range(4)], [blue, blue, red, red])
            self.assertEqual([s.rgb(x, 20) for x in range(3)], [(40, 0, 0), (0, 0, 0), (30, 0, 0)])

    def test_zrle_decompression_bomb_is_refused(self):
        with Session() as s:
            bomb = zlib.compress(b"\0" * 5_000_000)
            s.server.push(update(rect(0, 0, 1, 1, rfb.ZRLE, struct.pack(">I", len(bomb)) + bomb)))
            self.assertTrue(s.done.wait(5))
            self.assertIsInstance(s.error, rfb.RfbError)

    def test_cursor(self):
        with Session() as s:
            pixels = px(255, 0, 0) + px(0, 255, 0) + px(0, 0, 255) + px(10, 20, 30)
            mask = bytes([0b11000000, 0b10000000])
            s.push(update(rect(1, 2, 2, 2, rfb.PSEUDO_CURSOR, pixels + mask)), events=1)
            kind, w, h, hx, hy, rgba = s.handler.events[-1]
            self.assertEqual((kind, w, h, hx, hy), ("cursor", 2, 2, 1, 2))
            # RGBA; das letzte Pixel (zweite Zeile, zweite Spalte) ist maskiert, also durchsichtig
            self.assertEqual(rgba, bytes([255, 0, 0, 255, 0, 255, 0, 255, 0, 0, 255, 255, 10, 20, 30, 0]))
            s.push(update(rect(0, 0, 0, 0, rfb.PSEUDO_CURSOR)), events=1)
            self.assertEqual(s.handler.events[-1], ("cursor", 0, 0, 0, 0, None))

    def test_desktop_resize(self):
        with Session() as s:
            s.push(update(rect(0, 0, 60, 50, rfb.PSEUDO_DESKTOP_SIZE)))
            self.assertEqual(s.handler.events[-1], ("resize", 60, 50))
            self.assertEqual((s.client.width, s.client.height, len(s.client.fb)), (60, 50, 60 * 50 * 4))
            self.assertTrue(s.server.wait_for("update_request", 2))
            self.assertEqual(s.server.of("update_request")[1], (0, 0, 0, 60, 50))  # nach der Größenänderung das ganze Bild neu
            s.push(update(rect(58, 48, 2, 2, rfb.RAW, px(1, 2, 3) * 4)))
            self.assertEqual(s.rgb(59, 49), (1, 2, 3))

    def test_bell_and_clipboard(self):
        with Session() as s:
            s.push(b"\x02")
            self.assertEqual(s.handler.events[-1], ("bell",))
            text = "Grüße".encode("latin-1")
            s.push(struct.pack(">Bxxxi", 3, len(text)) + text)
            self.assertEqual(s.handler.events[-1], ("clipboard", "Grüße"))
            s.server.push(struct.pack(">Bxxxi", 3, -8) + bytes(8))  # erweiterte Zwischenablage: ignoriert, bleibt synchron
            s.push(b"\x02")
            self.assertEqual(s.handler.events[-1], ("bell",))

    def test_malformed_updates_end_the_connection(self):
        cases = {
            "Rechteck außerhalb": update(rect(90, 70, 20, 20, rfb.RAW, bytes(20 * 20 * 4))),
            "unbekannte Kodierung": update(rect(0, 0, 1, 1, 7)),
            "unbekannte Nachricht": b"\x63",
            "CopyRect außerhalb": update(rect(0, 0, 10, 10, rfb.COPYRECT, struct.pack(">HH", 95, 75))),
            "Hextile-Teilrechteck zu groß": update(rect(0, 0, 4, 4, rfb.HEXTILE, bytes([2 | 4 | 8]) + px(0, 0, 0) * 2 + bytes([1, 0x00, 0xFF]))),
        }
        for name, data in cases.items():
            with self.subTest(name), Session() as s:
                s.server.push(data)
                self.assertTrue(s.done.wait(5))
                self.assertIsInstance(s.error, rfb.RfbError)


class InputAndLifecycleTests(unittest.TestCase):
    def test_input_messages(self):
        with Session() as s:
            s.client.send_pointer(12, 34, 1)
            s.client.send_key(0xFF0D, True)
            s.client.send_key(0xFF0D, False)
            s.client.send_cut_text("Grüße €")
            self.assertTrue(s.server.wait_for("cut_text"))
            self.assertEqual(s.server.of("pointer"), [(1, 12, 34)])
            self.assertEqual(s.server.of("key"), [(1, 0xFF0D), (0, 0xFF0D)])
            self.assertEqual(s.server.of("cut_text"), [("Grüße ?",)])  # Latin-1: „€“ ist nicht darstellbar

    def test_close_ends_run_without_error(self):
        with Session() as s:
            s.client.close()
            self.assertTrue(s.done.wait(5))
            self.assertIsNone(s.error)

    def test_close_while_connecting_returns_quickly(self):
        silent = socket.socket()
        silent.bind(("127.0.0.1", 0))
        silent.listen(1)
        self.addCleanup(silent.close)
        client = rfb.Client("127.0.0.1", silent.getsockname()[1], timeout=30)
        errors = []

        def connect():
            try:
                client.connect()
            except rfb.RfbError as exc:
                errors.append(exc)

        thread = threading.Thread(target=connect, daemon=True)
        thread.start()
        time.sleep(0.3)  # der Server schweigt: der Handshake wartet
        client.close()
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(errors), 1)

    def test_dropped_connection_is_reported(self):
        with Session() as s:
            s.server.drop()
            self.assertTrue(s.done.wait(5))
            self.assertIsInstance(s.error, rfb.RfbError)


if __name__ == "__main__":
    unittest.main()
