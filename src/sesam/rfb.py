"""Eingebauter VNC-Client (RFB-Protokoll 3.3, 3.7 und 3.8), ohne Abhängigkeit von Qt.

Der Client hält das Bild in einem ``bytearray`` im Format BGRX (32 Bit, little endian, entspricht ``QImage.Format_RGB32``),
sodass die Oberfläche es ohne Kopie anzeigen kann. Anmeldung: ohne Passwort oder mit VNC-Passwort (DES-Antwort auf die
Zufallszahl des Servers). Kodierungen: Raw, CopyRect, Hextile und ZRLE sowie die Pseudo-Kodierungen Cursor und DesktopSize.
Nicht enthalten: TLS/VeNCrypt, Apple-ARD, MS-Logon, Tight, erweiterte Zwischenablage.

Alle Größen aus dem Netz werden geprüft (Bildgröße, Rechtecke, Textlängen, Entpacken): ein fehlerhafter oder böswilliger Server
kann den Client höchstens beenden, aber weder außerhalb des Bildes schreiben noch unbegrenzt Speicher belegen.
"""
from __future__ import annotations

import re
import socket
import struct
import threading
import zlib

from .i18n import tr

MAX_PIXELS = 50_000_000  # größtes erlaubtes Bild (Breite × Höhe)
MAX_TEXT = 1 << 20  # größter Text (Zwischenablage, Meldungen)
MAX_COMPRESSED = 64 << 20  # größter komprimierter ZRLE-Block
MAX_CURSOR = 256  # größerer Mauszeiger wird ignoriert

RAW, COPYRECT, HEXTILE, ZRLE = 0, 1, 5, 16
PSEUDO_CURSOR, PSEUDO_DESKTOP_SIZE = -239, -223
DEFAULT_ENCODINGS = (ZRLE, HEXTILE, COPYRECT, RAW, PSEUDO_CURSOR, PSEUDO_DESKTOP_SIZE)

SEC_NONE, SEC_VNC = 1, 2


class RfbError(Exception):
    """Verbindungs- oder Protokollfehler (die Meldung ist für den Benutzer bestimmt)."""


class PasswordRequired(RfbError):
    """Der Server verlangt ein Passwort, und es wurde keines angegeben."""


class AuthError(RfbError):
    """Das Passwort wurde abgelehnt."""


class UnsupportedError(RfbError):
    """Der Server bietet nur Anmeldearten an, die dieser Client nicht beherrscht."""


def des_cipher(key: bytes, mode):
    """DES (einfach) über die TripleDES-Klasse der cryptography-Bibliothek: drei gleiche Teilschlüssel."""
    from cryptography.hazmat.primitives.ciphers import Cipher

    try:  # ab cryptography 43 liegt TripleDES unter „decrepit“
        from cryptography.hazmat.decrepit.ciphers.algorithms import TripleDES
    except ImportError:
        from cryptography.hazmat.primitives.ciphers.algorithms import TripleDES
    if len(key) == 8:
        key *= 3  # drei gleiche Teilschlüssel = einfaches DES, ohne Warnung zu 8-Byte-Schlüsseln
    return Cipher(TripleDES(key), mode)


def vnc_response(password: str, challenge: bytes) -> bytes:
    """Antwort auf die 16 Byte Zufallszahl: DES mit dem Passwort als Schlüssel, in jedem Byte bitweise gespiegelt."""
    from cryptography.hazmat.primitives.ciphers import modes

    raw = password.encode("latin-1", "replace")[:8].ljust(8, b"\0")
    key = bytes(int(f"{b:08b}"[::-1], 2) for b in raw)
    enc = des_cipher(key, modes.ECB()).encryptor()  # codeql[py/weak-cryptographic-algorithm] DES/3DES ist durch Remmina-Profil und VNC-Protokoll vorgegeben
    return enc.update(bytes(challenge)) + enc.finalize()


class Handler:
    """Empfänger der Ereignisse (die Methoden laufen im Thread, der ``Client.run`` aufruft)."""

    def update(self, rects: list[tuple[int, int, int, int]]) -> None:
        """Geänderte Bereiche (x, y, Breite, Höhe) des Bildes."""

    def resize(self, width: int, height: int, fb: bytearray) -> None:
        """Der Server hat die Bildgröße geändert; ``fb`` ist der neue Bildspeicher."""

    def cursor(self, width: int, height: int, hot_x: int, hot_y: int, rgba: bytes | None) -> None:
        """Neuer Mauszeiger (RGBA); ``rgba`` ist ``None``, wenn der Server keinen Zeiger anzeigen will."""

    def bell(self) -> None:
        pass

    def clipboard(self, text: str) -> None:
        pass


class _Cursor:
    """Leser über die entpackten ZRLE-Daten."""

    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def take(self, n: int) -> bytes:
        end = self.pos + n
        if end > len(self.data):
            raise RfbError(_invalid("ZRLE"))
        chunk = self.data[self.pos:end]
        self.pos = end
        return chunk

    def byte(self) -> int:
        return self.take(1)[0]

    def run_length(self) -> int:
        total = 1
        while True:
            b = self.byte()
            total += b
            if b != 255:
                return total


def _invalid(what: str) -> str:
    return tr("Ungültige Daten vom Server: {what}", what=what)


_UNPACK = {bits: [tuple((b >> (8 - bits * (s + 1))) & ((1 << bits) - 1) for s in range(8 // bits)) for b in range(256)] for bits in (1, 2, 4)}
_PIXEL_FORMAT = struct.pack(">BBBBHHHBBB3x", 32, 24, 0, 1, 255, 255, 255, 16, 8, 0)  # BGRX, 32 Bit, little endian


class Client:
    def __init__(self, host: str, port: int, password: str | None = None, timeout: float = 10.0, encodings: tuple[int, ...] = DEFAULT_ENCODINGS):
        self.host, self.port, self.password, self.timeout, self.encodings = host, port, password, timeout, tuple(encodings)
        self.width = self.height = 0
        self.name = ""
        self.version = (3, 8)
        self.fb = bytearray()
        self._sock: socket.socket | None = None
        self._buf = bytearray()
        self._pos = 0
        self._send_lock = threading.Lock()
        self._user_closed = False
        self._zlib = zlib.decompressobj()

    # ------------------------------------------------------------ Verbindung

    def connect(self) -> None:
        """Verbindet und führt Handshake, Anmeldung und Initialisierung aus."""
        try:
            self._sock = socket.create_connection((self.host, self.port), self.timeout)
        except OSError as exc:
            raise RfbError(tr("Verbindung zu {host}:{port} fehlgeschlagen: {exc}", host=self.host, port=self.port, exc=exc)) from exc
        if self._user_closed:  # während des Verbindens beendet
            self.close()
            raise RfbError(tr("Verbindung vom Server geschlossen."))
        try:
            self._sock.settimeout(self.timeout)
            self._sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            self._handshake()
            self._sock.settimeout(None)
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        """Beendet die Verbindung (kann aus jedem Thread aufgerufen werden); ``run`` kehrt dann ohne Fehler zurück."""
        self._user_closed = True
        sock = self._sock
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass

    def _handshake(self) -> None:
        banner = self._read(12)
        m = re.fullmatch(rb"RFB (\d{3})\.(\d{3})\n", bytes(banner))
        if not m:
            raise RfbError(tr("Der Server antwortet nicht wie ein VNC-Server."))
        major, minor = int(m.group(1)), int(m.group(2))
        if major < 3:
            raise RfbError(tr("Der Server antwortet nicht wie ein VNC-Server."))
        self.version = (3, 8) if (major > 3 or minor >= 8) else (3, 7) if minor == 7 else (3, 3)
        self._send_raw(b"RFB 003.00%d\n" % self.version[1])

        if self.version == (3, 3):
            (stype,) = struct.unpack(">I", self._read(4))
            if stype == 0:
                raise RfbError(tr("Der Server lehnt die Verbindung ab: {reason}", reason=self._reason()))
            types = [stype]
        else:
            count = self._read(1)[0]
            if count == 0:
                raise RfbError(tr("Der Server lehnt die Verbindung ab: {reason}", reason=self._reason()))
            types = list(self._read(count))
        if self.password and SEC_VNC in types:
            chosen = SEC_VNC
        elif SEC_NONE in types:
            chosen = SEC_NONE
        elif SEC_VNC in types:
            chosen = SEC_VNC
        else:
            raise UnsupportedError(tr("Der eingebaute Viewer unterstützt die Anmeldeart des Servers nicht (Typen: {types}). Bitte einen anderen Viewer wählen.",
                                      types=", ".join(str(t) for t in types)))
        if chosen == SEC_VNC and not self.password:
            raise PasswordRequired(tr("Der Server verlangt ein Passwort."))
        if self.version != (3, 3):
            self._send_raw(bytes([chosen]))
        if chosen == SEC_VNC:
            self._send_raw(vnc_response(self.password, bytes(self._read(16))))
        if chosen == SEC_VNC or self.version == (3, 8):
            (result,) = struct.unpack(">I", self._read(4))
            if result != 0:
                reason = self._reason() if self.version == (3, 8) else ""
                raise AuthError(tr("Anmeldung fehlgeschlagen (Passwort falsch?).") + (f" ({reason})" if reason else ""))

        self._send_raw(b"\x01")  # ClientInit: Bildschirm mit anderen teilen
        width, height, _pf, name_len = struct.unpack(">HH16sI", self._read(24))
        if name_len > 4096:
            raise RfbError(_invalid("name"))
        self.name = bytes(self._read(name_len)).decode("utf-8", "replace")
        self._set_size(width, height)
        self._send_raw(b"\x00\x00\x00\x00" + _PIXEL_FORMAT)
        self._send_raw(struct.pack(">BxH", 2, len(self.encodings)) + b"".join(struct.pack(">i", e) for e in self.encodings))
        self.request_update(incremental=False)

    def _reason(self) -> str:
        (length,) = struct.unpack(">I", self._read(4))
        if length > MAX_TEXT:
            raise RfbError(_invalid("reason"))
        return bytes(self._read(length)).decode("utf-8", "replace")

    def _set_size(self, width: int, height: int) -> None:
        if not (0 < width and 0 < height and width * height <= MAX_PIXELS):
            raise RfbError(_invalid(f"{width}x{height}"))
        self.width, self.height = width, height
        self.fb = bytearray(width * height * 4)

    # ------------------------------------------------------------ Ein- und Ausgabe

    def _read(self, n: int) -> bytearray:
        buf = self._buf
        while len(buf) - self._pos < n:
            if self._pos:
                del buf[:self._pos]
                self._pos = 0
            try:
                chunk = self._sock.recv(max(65536, n - len(buf)))
            except OSError as exc:
                raise RfbError(tr("Verbindung vom Server geschlossen.")) from exc
            if not chunk:
                raise RfbError(tr("Verbindung vom Server geschlossen."))
            buf += chunk
        out = buf[self._pos:self._pos + n]
        self._pos += n
        return out

    def _send_raw(self, data: bytes) -> None:
        with self._send_lock:
            try:
                self._sock.sendall(data)
            except OSError:  # der Lese-Thread bemerkt den Abbruch und meldet ihn
                try:
                    self._sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    def request_update(self, incremental: bool = True) -> None:
        self._send_raw(struct.pack(">BBHHHH", 3, 1 if incremental else 0, 0, 0, self.width, self.height))

    def send_pointer(self, x: int, y: int, buttons: int) -> None:
        """``buttons``: Bitmaske (1 links, 2 Mitte, 4 rechts, 8/16 Rad hoch/runter, 32/64 Rad links/rechts)."""
        self._send_raw(struct.pack(">BBHH", 5, buttons & 0xFF, max(0, min(x, 65535)), max(0, min(y, 65535))))

    def send_key(self, keysym: int, down: bool) -> None:
        self._send_raw(struct.pack(">BBxxI", 4, 1 if down else 0, keysym & 0xFFFFFFFF))

    def send_cut_text(self, text: str) -> None:
        """Text für die Zwischenablage des Servers (das Standardprotokoll kennt nur Latin-1)."""
        data = text.encode("latin-1", "replace")[:MAX_TEXT]
        self._send_raw(struct.pack(">BxxxI", 6, len(data)) + data)

    # ------------------------------------------------------------ Empfangsschleife

    def run(self, handler: Handler | None = None) -> None:
        """Verarbeitet Nachrichten des Servers, bis die Verbindung endet. Fehler: ``RfbError``; nach ``close()`` kein Fehler."""
        handler = handler or Handler()
        try:
            while not self._user_closed:
                kind = self._read(1)[0]
                if kind == 0:
                    self._framebuffer_update(handler)
                elif kind == 1:  # Farbtabelle: bei True-Colour nicht benutzt
                    _, _first, count = struct.unpack(">BHH", self._read(5))
                    self._read(count * 6)
                elif kind == 2:
                    handler.bell()
                elif kind == 3:
                    self._read(3)
                    (length,) = struct.unpack(">i", self._read(4))
                    if abs(length) > MAX_TEXT:
                        raise RfbError(_invalid("clipboard"))
                    data = self._read(abs(length))
                    if length >= 0:  # negativ: erweiterte Zwischenablage, wird nicht ausgewertet
                        handler.clipboard(bytes(data).decode("latin-1"))
                else:
                    raise RfbError(_invalid(f"message {kind}"))
        except RfbError:
            if not self._user_closed:
                raise

    def _framebuffer_update(self, handler: Handler) -> None:
        self._read(1)
        (count,) = struct.unpack(">H", self._read(2))
        dirty: list[tuple[int, int, int, int]] = []
        resized = False
        for _ in range(count):
            x, y, w, h, enc = struct.unpack(">HHHHi", self._read(12))
            if enc == PSEUDO_CURSOR:
                self._cursor(handler, w, h, x, y)
                continue
            if enc == PSEUDO_DESKTOP_SIZE:
                self._set_size(w, h)
                handler.resize(self.width, self.height, self.fb)
                dirty.clear()
                resized = True
                continue
            if x + w > self.width or y + h > self.height:
                raise RfbError(_invalid("rectangle"))
            if enc == RAW:
                self._blit(x, y, w, h, self._read(w * h * 4))
            elif enc == COPYRECT:
                sx, sy = struct.unpack(">HH", self._read(4))
                self._copy(sx, sy, x, y, w, h)
            elif enc == HEXTILE:
                self._hextile(x, y, w, h)
            elif enc == ZRLE:
                self._zrle(x, y, w, h)
            else:
                raise RfbError(_invalid(f"encoding {enc}"))
            if w and h:
                dirty.append((x, y, w, h))
        if dirty:
            handler.update(dirty)
        self.request_update(incremental=not resized)

    def _cursor(self, handler: Handler, w: int, h: int, hot_x: int, hot_y: int) -> None:
        mask_row = (w + 7) // 8
        if w > MAX_CURSOR or h > MAX_CURSOR:
            self._read(w * h * 4 + mask_row * h)
            return
        if not w or not h:
            handler.cursor(0, 0, 0, 0, None)
            return
        pixels = self._read(w * h * 4)
        mask = self._read(mask_row * h)
        rgba = bytearray(w * h * 4)
        rgba[0::4], rgba[1::4], rgba[2::4] = pixels[2::4], pixels[1::4], pixels[0::4]
        for r in range(h):
            for c in range(w):
                if mask[r * mask_row + c // 8] >> (7 - c % 8) & 1:
                    rgba[(r * w + c) * 4 + 3] = 255
        handler.cursor(w, h, hot_x, hot_y, bytes(rgba))

    # ------------------------------------------------------------ Bildoperationen (Bereiche sind vorher geprüft)

    def _blit(self, x: int, y: int, w: int, h: int, data) -> None:
        stride = self.width * 4
        if w == self.width:
            self.fb[y * stride:(y + h) * stride] = data
            return
        row = w * 4
        for r in range(h):
            o = (y + r) * stride + x * 4
            self.fb[o:o + row] = data[r * row:(r + 1) * row]

    def _fill(self, x: int, y: int, w: int, h: int, pixel: bytes) -> None:
        stride = self.width * 4
        if w == self.width:
            self.fb[y * stride:(y + h) * stride] = pixel * (w * h)
            return
        row = pixel * w
        for r in range(h):
            o = (y + r) * stride + x * 4
            self.fb[o:o + w * 4] = row

    def _copy(self, sx: int, sy: int, x: int, y: int, w: int, h: int) -> None:
        if sx + w > self.width or sy + h > self.height:
            raise RfbError(_invalid("copyrect"))
        stride = self.width * 4
        rows = [bytes(self.fb[(sy + r) * stride + sx * 4:(sy + r) * stride + (sx + w) * 4]) for r in range(h)]  # erst lesen: Quelle und Ziel dürfen überlappen
        for r, row in enumerate(rows):
            o = (y + r) * stride + x * 4
            self.fb[o:o + w * 4] = row

    def _hextile(self, x: int, y: int, w: int, h: int) -> None:
        background = foreground = b"\0\0\0\0"
        for ty in range(y, y + h, 16):
            th = min(16, y + h - ty)
            for tx in range(x, x + w, 16):
                tw = min(16, x + w - tx)
                sub = self._read(1)[0]
                if sub & 1:
                    self._blit(tx, ty, tw, th, self._read(tw * th * 4))
                    continue
                if sub & 2:
                    background = bytes(self._read(4))
                if sub & 4:
                    foreground = bytes(self._read(4))
                self._fill(tx, ty, tw, th, background)
                if not sub & 8:
                    continue
                count = self._read(1)[0]
                coloured = bool(sub & 16)
                data = self._read(count * (6 if coloured else 2))
                i = 0
                for _ in range(count):
                    if coloured:
                        pixel = bytes(data[i:i + 4])
                        i += 4
                    else:
                        pixel = foreground
                    xy, wh = data[i], data[i + 1]
                    i += 2
                    sx, sy, sw, sh = tx + (xy >> 4), ty + (xy & 15), (wh >> 4) + 1, (wh & 15) + 1
                    if sx + sw > tx + tw or sy + sh > ty + th:
                        raise RfbError(_invalid("hextile"))
                    self._fill(sx, sy, sw, sh, pixel)

    def _zrle(self, x: int, y: int, w: int, h: int) -> None:
        (length,) = struct.unpack(">I", self._read(4))
        if length > MAX_COMPRESSED:
            raise RfbError(_invalid("zrle"))
        compressed = bytes(self._read(length))
        tiles = ((w + 63) // 64) * ((h + 63) // 64)
        try:
            raw = self._zlib.decompress(compressed, w * h * 4 + tiles * 1024 + 4096)
        except zlib.error as exc:
            raise RfbError(_invalid("zrle")) from exc
        if self._zlib.unconsumed_tail:
            raise RfbError(_invalid("zrle"))
        src = _Cursor(raw)
        for ty in range(y, y + h, 64):
            th = min(64, y + h - ty)
            for tx in range(x, x + w, 64):
                self._zrle_tile(src, tx, ty, min(64, x + w - tx), th)

    def _zrle_tile(self, src: _Cursor, tx: int, ty: int, tw: int, th: int) -> None:
        n = tw * th
        sub = src.byte()
        if sub == 0:  # unkomprimiert, 3 Byte je Pixel
            data = src.take(n * 3)
            out = bytearray(n * 4)
            out[0::4], out[1::4], out[2::4], out[3::4] = data[0::3], data[1::3], data[2::3], b"\xff" * n
            self._blit(tx, ty, tw, th, out)
        elif sub == 1:  # einfarbig
            self._fill(tx, ty, tw, th, src.take(3) + b"\xff")
        elif 2 <= sub <= 16:  # gepackte Palette
            palette = [src.take(3) + b"\xff" for _ in range(sub)]
            bits = 1 if sub == 2 else 2 if sub <= 4 else 4
            row_bytes = (tw * bits + 7) // 8
            table = _UNPACK[bits]
            out = bytearray()
            try:
                for _ in range(th):
                    indices = [i for b in src.take(row_bytes) for i in table[b]][:tw]
                    out += b"".join(palette[i] for i in indices)
            except IndexError as exc:
                raise RfbError(_invalid("zrle")) from exc
            self._blit(tx, ty, tw, th, out)
        elif sub == 128 or sub >= 130:  # lauflängenkodiert, mit oder ohne Palette
            palette = [src.take(3) + b"\xff" for _ in range(sub - 128)] if sub >= 130 else None
            out = bytearray()
            total = n * 4
            while len(out) < total:
                if palette is None:
                    pixel = src.take(3) + b"\xff"
                    run = src.run_length()
                else:
                    b = src.byte()
                    if (b & 0x7F) >= len(palette):
                        raise RfbError(_invalid("zrle"))
                    pixel = palette[b & 0x7F]
                    run = src.run_length() if b & 0x80 else 1
                if len(out) + run * 4 > total:
                    raise RfbError(_invalid("zrle"))
                out += pixel * run
            self._blit(tx, ty, tw, th, out)
        else:
            raise RfbError(_invalid("zrle"))
