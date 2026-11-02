"""Fenster des eingebauten VNC-Viewers (Protokoll: ``sesam.rfb``)."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QThread, Signal
from PySide6.QtGui import QAction, QColor, QCursor, QFont, QGuiApplication, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QInputDialog, QMainWindow, QScrollArea, QToolBar, QWidget

from .. import rfb
from ..i18n import tr
from . import style


def _i(key) -> int:
    return int(getattr(key, "value", key))


_K = Qt.Key
_SPECIAL = {_i(k): v for k, v in {
    _K.Key_Return: 0xFF0D, _K.Key_Enter: 0xFF8D, _K.Key_Backspace: 0xFF08, _K.Key_Tab: 0xFF09, _K.Key_Backtab: 0xFF09,
    _K.Key_Escape: 0xFF1B, _K.Key_Delete: 0xFFFF, _K.Key_Insert: 0xFF63, _K.Key_Home: 0xFF50, _K.Key_Left: 0xFF51,
    _K.Key_Up: 0xFF52, _K.Key_Right: 0xFF53, _K.Key_Down: 0xFF54, _K.Key_PageUp: 0xFF55, _K.Key_PageDown: 0xFF56,
    _K.Key_End: 0xFF57, _K.Key_Shift: 0xFFE1, _K.Key_Control: 0xFFE3, _K.Key_Alt: 0xFFE9, _K.Key_Meta: 0xFFEB,
    _K.Key_CapsLock: 0xFFE5, _K.Key_NumLock: 0xFF7F, _K.Key_ScrollLock: 0xFF14, _K.Key_Print: 0xFF61,
    _K.Key_Pause: 0xFF13, _K.Key_Menu: 0xFF67, _K.Key_SysReq: 0xFF15,
}.items()}
_F1, _F35 = _i(_K.Key_F1), _i(_K.Key_F35)
_IGNORED = {_i(_K.Key_AltGr), _i(_K.Key_Mode_switch)}  # das fertige Zeichen wird gesendet, AltGr selbst nicht
_MODIFIER_KEYSYMS = (0xFFE1, 0xFFE3, 0xFFE9, 0xFFEB)

BUTTONS = {_i(Qt.MouseButton.LeftButton): 1, _i(Qt.MouseButton.MiddleButton): 2, _i(Qt.MouseButton.RightButton): 4}
WHEEL_UP, WHEEL_DOWN, WHEEL_LEFT, WHEEL_RIGHT = 8, 16, 32, 64


def keysym_for(key: int, text: str, shift: bool) -> int | None:
    """X11-Keysym für eine Qt-Taste (``None``: nicht senden)."""
    if key in _IGNORED:
        return None
    if key in _SPECIAL:
        return _SPECIAL[key]
    if _F1 <= key <= _F35:
        return 0xFFBE + key - _F1
    if len(text) == 1 and text >= " " and text != "\x7f":
        cp = ord(text)
        return cp if cp < 0x100 else 0x01000000 + cp
    if 0x20 <= key <= 0x7E:  # z. B. Strg+A: der Text ist ein Steuerzeichen
        ch = chr(key)
        return ord(ch if shift or not ch.isalpha() else ch.lower())
    return None


_RUNNING: set = set()  # noch laufende Lese-Threads geschlossener Fenster


class _Reader(QThread, rfb.Handler):
    """Verbindet und liest im Hintergrund; alle Ergebnisse kommen als Signale in den Oberflächen-Thread."""

    connected = Signal(int, int, str, object)
    failed = Signal(str, str)  # Art („password“, „auth“, „error“), Meldung
    ended = Signal(str)
    updated = Signal(object)
    resized = Signal(int, int, object)
    cursor_changed = Signal(object)
    belled = Signal()
    clipboard_changed = Signal(str)

    def __init__(self, host: str, port: int, password: str | None):
        super().__init__()
        self.client = rfb.Client(host, port, password)

    def stop(self) -> None:
        self.client.close()

    def run(self) -> None:
        client = self.client
        try:
            client.connect()
        except rfb.PasswordRequired as exc:
            self.failed.emit("password", str(exc))
            return
        except rfb.AuthError as exc:
            self.failed.emit("auth", str(exc))
            return
        except rfb.RfbError as exc:
            self.failed.emit("error", str(exc))
            return
        self.connected.emit(client.width, client.height, client.name, client.fb)
        try:
            client.run(self)
        except rfb.RfbError as exc:
            self.ended.emit(str(exc))
            return
        self.ended.emit("")

    # --- rfb.Handler (läuft in diesem Thread)
    def update(self, rects) -> None:
        self.updated.emit(rects)

    def resize(self, width, height, fb) -> None:
        self.resized.emit(width, height, fb)

    def cursor(self, width, height, hot_x, hot_y, rgba) -> None:
        self.cursor_changed.emit(None if rgba is None else (width, height, hot_x, hot_y, rgba))

    def bell(self) -> None:
        self.belled.emit()

    def clipboard(self, text) -> None:
        self.clipboard_changed.emit(text)


class RemoteView(QWidget):
    """Zeigt das Bild (skaliert oder in Originalgröße) und gibt Maus und Tastatur an das Fenster weiter."""

    def __init__(self, owner: VncWindow):
        super().__init__()
        self.owner = owner
        self.image: QImage | None = None
        self._fb = None
        self.fit = True
        self.message = ""
        self.dimmed = False
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setMinimumSize(1, 1)

    def set_image(self, fb, width: int, height: int) -> None:
        self._fb = fb  # der QImage nutzt den Speicher des Clients, ohne zu kopieren
        self.image = QImage(fb, width, height, width * 4, QImage.Format.Format_RGB32)
        self.message = ""
        self.dimmed = False
        self.apply_mode()
        self.update()

    def set_message(self, text: str, dimmed: bool = False) -> None:
        self.message, self.dimmed = text, dimmed
        self.update()

    def apply_mode(self) -> None:
        if self.fit or self.image is None:
            self.setMinimumSize(1, 1)
            self.setMaximumSize(16777215, 16777215)
        else:
            self.setFixedSize(self.image.size())

    def sizeHint(self) -> QSize:
        return self.image.size() if self.image is not None else QSize(640, 400)

    def _geometry(self) -> tuple[float, float, float]:
        """(Versatz x, Versatz y, Maßstab) des Bildes im Widget."""
        if self.image is None:
            return 0.0, 0.0, 1.0
        w, h = self.image.width(), self.image.height()
        if not self.fit:
            return 0.0, 0.0, 1.0
        scale = min(self.width() / w, self.height() / h)
        return (self.width() - w * scale) / 2, (self.height() - h * scale) / 2, scale

    def invalidate(self, rects) -> None:
        ox, oy, s = self._geometry()
        for x, y, w, h in rects:
            self.update(int(ox + x * s) - 1, int(oy + y * s) - 1, int(w * s) + 3, int(h * s) + 3)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#1b1b1f"))
        if self.image is not None:
            ox, oy, s = self._geometry()
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, s < 1)
            painter.drawImage(QRectF(ox, oy, self.image.width() * s, self.image.height() * s), self.image)
        if self.dimmed:
            painter.fillRect(self.rect(), QColor(0, 0, 0, 140))
        if self.message:
            painter.setPen(QColor("#e6e6e6"))
            font = QFont(self.font())
            font.setPointSize(font.pointSize() + 2)
            painter.setFont(font)
            painter.drawText(self.rect().adjusted(20, 20, -20, -20), int(Qt.AlignmentFlag.AlignCenter) | int(Qt.TextFlag.TextWordWrap), self.message)

    # --- Eingabe
    def _remote_pos(self, pos: QPointF) -> tuple[int, int] | None:
        if self.image is None:
            return None
        ox, oy, s = self._geometry()
        x = int((pos.x() - ox) / s)
        y = int((pos.y() - oy) / s)
        return max(0, min(x, self.image.width() - 1)), max(0, min(y, self.image.height() - 1))

    def _pointer(self, event, extra: int = 0) -> None:
        pos = self._remote_pos(event.position())
        if pos is not None:
            mask = 0
            for qt_button, bit in BUTTONS.items():
                if _i(event.buttons()) & qt_button:
                    mask |= bit
            self.owner.send_pointer(pos[0], pos[1], mask | extra)

    def mousePressEvent(self, event) -> None:
        self.setFocus()
        self._pointer(event)

    def mouseReleaseEvent(self, event) -> None:
        self._pointer(event)

    def mouseMoveEvent(self, event) -> None:
        self._pointer(event)

    def mouseDoubleClickEvent(self, event) -> None:
        self._pointer(event)

    def wheelEvent(self, event) -> None:
        delta = event.angleDelta()
        for amount, up, down in ((delta.y(), WHEEL_UP, WHEEL_DOWN), (delta.x(), WHEEL_RIGHT, WHEEL_LEFT)):
            for _ in range(max(1, abs(amount) // 120) if amount else 0):
                self._pointer(event, up if amount > 0 else down)
                self._pointer(event)

    def keyPressEvent(self, event) -> None:
        self.owner.key_event(event, True)

    def keyReleaseEvent(self, event) -> None:
        self.owner.key_event(event, False)

    def focusNextPrevChild(self, next_: bool) -> bool:
        return False  # Tab gehört dem entfernten Rechner

    def focusOutEvent(self, event) -> None:
        self.owner.release_keys()
        super().focusOutEvent(event)

    def event(self, event) -> bool:
        if event.type() == event.Type.ShortcutOverride and self.owner.captures_keys():
            event.accept()  # Tastenkürzel (außer F11) gehen an den entfernten Rechner
            return True
        return super().event(event)


class VncWindow(QMainWindow):
    """Ein VNC-Bildschirm in einem eigenen Fenster; der Tunnel (falls nötig) gehört dem Fernzugriff-Manager."""

    reconnect_requested = Signal()

    def __init__(self, name: str, host: str, port: int, password: str | None, direct: bool, parent=None):
        super().__init__(parent)
        self.name, self.host, self.port, self.direct = name, host, port, direct
        self._open = True
        self._first = True
        self._pressed: dict[int, int] = {}
        self._clip_known = ""  # zuletzt gesendeter oder empfangener Text: Wiederholungen und Rückkopplung vermeiden
        self.view_only = False
        self.reader: _Reader | None = None
        self.setWindowTitle(tr("{name} – VNC", name=name))
        self.setWindowIcon(style.app_icon())
        self.view = RemoteView(self)
        self.scroll = QScrollArea()
        self.scroll.setWidget(self.view)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.setCentralWidget(self.scroll)

        bar = QToolBar()
        bar.setMovable(False)
        bar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.addToolBar(bar)
        self.a_fit = self._action(bar, tr("An Fenster anpassen"), checkable=True, checked=True)
        self.a_fit.toggled.connect(self._set_fit)
        self.a_actual = self._action(bar, tr("100 %"))
        self.a_actual.triggered.connect(lambda: self.a_fit.setChecked(False))
        self.a_view_only = self._action(bar, tr("Nur Ansicht"), checkable=True)
        self.a_view_only.toggled.connect(self._set_view_only)
        self.a_clipboard = self._action(bar, tr("Zwischenablage abgleichen"), checkable=True, checked=True)
        self.a_cad = self._action(bar, tr("Strg+Alt+Entf"))
        self.a_cad.triggered.connect(self.send_ctrl_alt_del)
        self.a_full = self._action(bar, tr("Vollbild"), checkable=True)
        self.a_full.setShortcut("F11")
        self.a_full.toggled.connect(lambda on: self.showFullScreen() if on else self.showNormal())
        self.a_reconnect = self._action(bar, tr("Neu verbinden"))
        self.a_reconnect.setEnabled(False)
        self.a_reconnect.triggered.connect(self.reconnect_requested.emit)

        self.status_text = QLabel("")
        self.security_text = QLabel(tr("Direkt, unverschlüsselt") if direct else tr("Verschlüsselt durch den SSH-Tunnel"))
        self.statusBar().addWidget(self.status_text, 1)
        self.statusBar().addPermanentWidget(self.security_text)
        self.resize(900, 620)
        QGuiApplication.clipboard().dataChanged.connect(self._local_clipboard_changed)
        self._clipboard_connected = True
        self._start(password)

    def _action(self, bar: QToolBar, text: str, checkable: bool = False, checked: bool = False) -> QAction:
        action = QAction(text, self)
        action.setCheckable(checkable)
        action.setChecked(checked)
        bar.addAction(action)
        return action

    # --- Verbindung
    def _start(self, password: str | None) -> None:
        self.a_reconnect.setEnabled(False)
        self.status_text.setText(tr("Verbinde …"))
        self.view.set_message(tr("Verbinde mit {name} …", name=self.name))
        reader = self.reader = _Reader(self.host, self.port, password)
        reader.connected.connect(self._on_connected)
        reader.failed.connect(self._on_failed)
        reader.ended.connect(self._on_ended)
        reader.updated.connect(self.view.invalidate)
        reader.resized.connect(self._on_resized)
        reader.cursor_changed.connect(self._on_cursor)
        reader.belled.connect(QApplication.beep)
        reader.clipboard_changed.connect(self._on_remote_clipboard)
        reader.start()

    def _stop_reader(self) -> None:
        reader, self.reader = self.reader, None
        if reader is not None:
            reader.stop()
            if not reader.wait(3000):  # hängt noch im Verbindungsaufbau: nicht zerstören, solange der Thread läuft
                _RUNNING.add(reader)
                reader.finished.connect(lambda r=reader: _RUNNING.discard(r))

    def _on_connected(self, width: int, height: int, name: str, fb) -> None:
        if not self._open:
            return
        self.view.set_image(fb, width, height)
        self.status_text.setText(f"{name} · {width}×{height}" if name else f"{width}×{height}")
        if self._first:
            self._first = False
            screen = self.screen().availableGeometry() if self.screen() else None
            if screen is not None:
                self.resize(min(max(width + 4, 720), int(screen.width() * 0.9)), min(max(height + 70, 420), int(screen.height() * 0.9)))  # mindestens so breit, dass die Symbolleiste passt
        self.view.setFocus()
        self._clip_known = QGuiApplication.clipboard().text()  # was schon vorher in der Ablage lag, geht nicht automatisch hinaus

    def _on_resized(self, width: int, height: int, fb) -> None:
        self.view.set_image(fb, width, height)
        self.status_text.setText(f"{width}×{height}")

    def _on_failed(self, kind: str, message: str) -> None:
        if not self._open:
            return
        self.reader = None
        if kind in ("password", "auth"):
            text, ok = QInputDialog.getText(self, tr("Passwort für {name}", name=self.name), message, QLineEdit.EchoMode.Password)
            if ok and text:
                self._start(text)
                return
        self.view.set_message(tr("Nicht verbunden: {error}", error=message), dimmed=self.view.image is not None)
        self.status_text.setText(message)
        self.a_reconnect.setEnabled(True)

    def _on_ended(self, message: str) -> None:
        if not self._open:
            return
        self.release_keys(send=False)
        text = tr("Verbindung getrennt: {error}", error=message) if message else tr("Verbindung getrennt.")
        self.view.set_message(text, dimmed=True)
        self.status_text.setText(text)
        self.a_reconnect.setEnabled(True)

    def _on_cursor(self, info) -> None:
        if info is None:
            self.view.setCursor(Qt.CursorShape.BlankCursor)
            return
        w, h, hot_x, hot_y, rgba = info
        image = QImage(rgba, w, h, w * 4, QImage.Format.Format_RGBA8888).copy()
        self.view.setCursor(QCursor(QPixmap.fromImage(image), hot_x, hot_y))

    # --- Eingabe
    def _client(self) -> rfb.Client | None:
        return self.reader.client if self.reader is not None and self.view.image is not None and not self.a_reconnect.isEnabled() else None

    def captures_keys(self) -> bool:
        return self._client() is not None and not self.view_only

    def send_pointer(self, x: int, y: int, buttons: int) -> None:
        client = self._client()
        if client is not None and not self.view_only:
            client.send_pointer(x, y, buttons)

    def key_event(self, event, down: bool) -> None:
        client = self._client()
        if client is None or self.view_only:
            return
        key = event.key()
        if down:
            sym = keysym_for(key, event.text(), bool(_i(event.modifiers()) & _i(Qt.KeyboardModifier.ShiftModifier)))
            if sym is None:
                return
            self._pressed[key] = sym
        else:
            if event.isAutoRepeat():
                return
            sym = self._pressed.pop(key, None)
            if sym is None:
                return
        client.send_key(sym, down)

    def release_keys(self, send: bool = True) -> None:
        """Lässt alle gedrückten Tasten los (Fokusverlust), damit keine Umschalttaste auf dem anderen Rechner hängen bleibt."""
        pressed, self._pressed = self._pressed, {}
        client = self._client() if send else None
        if client is not None:
            for sym in pressed.values():
                client.send_key(sym, False)

    def send_ctrl_alt_del(self) -> None:
        client = self._client()
        if client is None or self.view_only:
            return
        for sym in (0xFFE3, 0xFFE9, 0xFFFF):
            client.send_key(sym, True)
        for sym in (0xFFFF, 0xFFE9, 0xFFE3):
            client.send_key(sym, False)

    def _set_fit(self, on: bool) -> None:
        self.view.fit = on
        self.scroll.setWidgetResizable(on)
        self.view.apply_mode()
        self.view.update()

    def _set_view_only(self, on: bool) -> None:
        self.view_only = on
        if on:
            self.release_keys()

    # --- Zwischenablage (nur Text; Änderungen gehen beim Kopieren bzw. beim Aktivieren des Fensters hinaus, solange der Abgleich eingeschaltet ist)
    def _on_remote_clipboard(self, text: str) -> None:
        if self.a_clipboard.isChecked() and text and not self.view_only:
            self._clip_known = text
            QGuiApplication.clipboard().setText(text)

    def _local_clipboard_changed(self) -> None:
        if self._open and self.isActiveWindow():
            self._send_clipboard()

    def _send_clipboard(self) -> None:
        client = self._client()
        if client is None or self.view_only or not self.a_clipboard.isChecked():
            return
        text = QGuiApplication.clipboard().text()
        if text and text != self._clip_known:
            self._clip_known = text
            client.send_cut_text(text)

    def changeEvent(self, event) -> None:
        if event.type() == event.Type.ActivationChange and self.isActiveWindow():
            self._send_clipboard()
        super().changeEvent(event)

    # --- Lebensdauer
    def is_open(self) -> bool:
        return self._open

    def end(self) -> None:
        """Beendet Verbindung und Fenster (vom Fernzugriff-Manager aufgerufen)."""
        self.close()

    def closeEvent(self, event) -> None:
        self._open = False
        if self._clipboard_connected:
            self._clipboard_connected = False
            QGuiApplication.clipboard().dataChanged.disconnect(self._local_clipboard_changed)
        self._stop_reader()
        super().closeEvent(event)
