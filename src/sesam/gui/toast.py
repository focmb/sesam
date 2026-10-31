"""Meldungen als Einblendung (Toast) statt in der Statuszeile.

Die Statuszeile behält Version und WireGuard-Anzeige; alle übrigen Meldungen erscheinen, wenn in den Einstellungen
aktiviert, als kleine Einblendung unten rechts im Fenster. Ein Klick schließt sie."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtWidgets import QLabel, QMainWindow

from . import theme

DEFAULT_TIMEOUT = 6000  # Meldungen ohne eigene Dauer; Texte mit „…“ (läuft noch) bleiben bis zur nächsten Meldung
ERROR_TIMEOUT = 10000
MARGIN = 14
MAX_WIDTH = 460


class Toast(QLabel):
    """Die Einblendung selbst: Klartext (fremde Texte werden nie als HTML gedeutet), Klick schließt."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("toast")
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setWordWrap(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.hide()

    def mousePressEvent(self, event):
        self.hide()


class ToastHost(QObject):
    """Zeigt Meldungen eines Fensters als Toast oder – bei ausgeschalteter Option – in dessen Statuszeile."""

    def __init__(self, window: QMainWindow, enabled):
        super().__init__(window)
        self._window = window
        self._enabled = enabled  # Funktion ohne Argumente: Option gerade aktiv?
        self._toast = Toast(window)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.clear)
        window.installEventFilter(self)

    def active(self) -> bool:
        return bool(self._enabled())

    def notify(self, text: str, timeout: int = 0, error: bool = False) -> None:
        """Zeigt ``text``. ``timeout`` in ms; 0 = Standarddauer (bzw. bis zur nächsten Meldung bei Texten mit „…“)."""
        if not self.active():
            self.clear()
            self._window.statusBar().showMessage(text, timeout)
            return
        if not text:
            self.clear()
            return
        self._window.statusBar().clearMessage()
        if not timeout:
            timeout = 0 if text.rstrip().endswith("…") else (ERROR_TIMEOUT if error else DEFAULT_TIMEOUT)
        self._style(error)
        self._toast.setText(text)
        self._fit()
        self._toast.show()
        self._toast.raise_()
        self._timer.stop()
        if timeout > 0:
            self._timer.start(timeout)

    def clear(self) -> None:
        self._timer.stop()
        self._toast.hide()

    def _style(self, error: bool) -> None:
        c = theme.colors()
        bg, border = (c["warn_bg"], c["danger"]) if error else (c["surface"], c["border"])
        self._toast.setStyleSheet(
            f"QLabel#toast {{ background: {bg}; color: {c['text']}; border: 1px solid {border}; border-radius: 8px; padding: 9px 14px; }}")

    def _fit(self) -> None:
        """Größe und Lage: unten rechts im Inhaltsbereich, nie breiter als das Fenster."""
        area = self._window.centralWidget()
        if area is None:
            return
        width = min(MAX_WIDTH, max(200, area.width() - 2 * MARGIN))
        self._toast.setFixedWidth(width)
        self._toast.setFixedHeight(self._toast.heightForWidth(width))
        corner = area.geometry().bottomRight()
        self._toast.move(corner.x() - width - MARGIN + 1, corner.y() - self._toast.height() - MARGIN + 1)

    def eventFilter(self, obj, event):
        if obj is self._window and event.type() == QEvent.Type.Resize and self._toast.isVisible():
            self._fit()
        return False
