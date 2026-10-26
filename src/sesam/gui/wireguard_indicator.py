"""WireGuard-Anzeige in der Statuszeile: Pille mit Schild, Tunnelname und Zustand; ein Klick zeigt die Einzelheiten."""
from __future__ import annotations

from PySide6.QtCore import QPoint, QSize, Qt, QTimer
from PySide6.QtWidgets import QFrame, QGridLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .. import wireguard
from ..i18n import tr
from ..runtime import IS_WINDOWS
from ..wireguard import CONNECTED, OFF, STALE, UNKNOWN, Status
from . import style, theme
from .jobs import Job
from .sftp_window import format_size

POLL_MS = 15000 if IS_WINDOWS else 5000  # unter Windows startet jede Abfrage PowerShell
# Zustand → (Icon, Farbwert aus dem Farbschema)
LOOK = {
    CONNECTED: ("shield_ok", "ok"),
    STALE: ("shield_warn", "warn"),
    OFF: ("shield_off", "muted"),
    UNKNOWN: ("shield_unknown", "muted"),
}


def tunnel_names(status: Status) -> str:
    """„VZS“, bei mehreren Tunneln „VZS +1“."""
    if not status.tunnels:
        return ""
    extra = len(status.tunnels) - 1
    return status.tunnels[0].name + (f" +{extra}" if extra else "")


def label_text(status: Status, last_name: str = "") -> str:
    if status.state == CONNECTED:
        return tr("WireGuard · {name} · verbunden", name=tunnel_names(status))
    if status.state == STALE:
        return tr("WireGuard · {name} · veraltet", name=tunnel_names(status))
    if status.state == OFF:
        return tr("WireGuard · {name} · getrennt", name=last_name) if last_name else tr("WireGuard · getrennt")
    return tr("WireGuard · nicht verfügbar")


def age_text(age: float) -> str:
    if age == wireguard.NEVER:
        return tr("noch keiner")
    seconds = int(age)
    if seconds < 60:
        return tr("vor {n} s", n=seconds)
    if seconds < 3600:
        return tr("vor {n} min", n=seconds // 60)
    return tr("vor {n} h", n=seconds // 3600)


class WireGuardPopup(QWidget):
    """Kleines Fenster mit den Einzelheiten; schließt sich bei einem Klick daneben oder mit Esc."""

    def __init__(self, status: Status, parent=None):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        box = QFrame()
        box.setObjectName("wgPopup")
        outer.addWidget(box)
        lay = QVBoxLayout(box)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(10)

        titles = {
            CONNECTED: tr("WireGuard-Tunnel aktiv"),
            STALE: tr("WireGuard-Tunnel: Handshake veraltet"),
            OFF: tr("Kein WireGuard-Tunnel aktiv"),
            UNKNOWN: tr("WireGuard-Status nicht verfügbar"),
        }
        title = QLabel(titles[status.state])
        title.setObjectName("wgPopupTitle")
        lay.addWidget(title)
        if status.state == UNKNOWN:
            lay.addWidget(self._muted(tr("Der Zustand lässt sich auf diesem System nicht ermitteln.")))
        for tunnel in status.tunnels:
            lay.addWidget(self._tunnel_box(tunnel, many=len(status.tunnels) > 1))
        if status.tunnels and not any(t.handshake_age is not None for t in status.tunnels):
            lay.addWidget(self._muted(tr("Endpunkt, Handshake und Datenmenge liefert WireGuard nur mit Administratorrechten.")))
        self.setFixedWidth(max(340, self.sizeHint().width()))

    @staticmethod
    def _muted(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("wgPopupHint")
        label.setWordWrap(True)
        return label

    @staticmethod
    def _tunnel_box(tunnel: wireguard.Tunnel, many: bool) -> QWidget:
        box = QWidget()
        grid = QGridLayout(box)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(6)
        rows = [(tr("Tunnel"), tunnel.name)]
        if tunnel.endpoint:
            rows.append((tr("Endpunkt"), tunnel.endpoint))
        if tunnel.addresses:
            rows.append((tr("Tunnel-Adresse"), ", ".join(tunnel.addresses)))
        if tunnel.allowed_ips:
            rows.append((tr("Erlaubte IPs"), ", ".join(tunnel.allowed_ips)))
        if tunnel.handshake_age is not None:
            rows.append((tr("Letzter Handshake"), age_text(tunnel.handshake_age)))
        if tunnel.rx is not None and tunnel.tx is not None:
            rows.append((tr("Empfangen / Gesendet"), f"{format_size(tunnel.rx)} / {format_size(tunnel.tx)}"))
        for i, (name, value) in enumerate(rows):
            key = QLabel(name)
            key.setObjectName("wgPopupHint")
            val = QLabel(value)
            val.setWordWrap(True)
            val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            grid.addWidget(key, i, 0, Qt.AlignmentFlag.AlignTop)
            grid.addWidget(val, i, 1)
        grid.setColumnStretch(1, 1)
        return box

    def show_above(self, anchor: QWidget):
        """Rechtsbündig über dem Anker (die Statuszeile liegt ganz unten im Fenster)."""
        self.adjustSize()
        top_right = anchor.mapToGlobal(QPoint(anchor.width(), 0))
        self.move(top_right.x() - self.width(), top_right.y() - self.height() - 6)
        self.show()


class WireGuardIndicator(QPushButton):
    """Zeigt, ob dieser Rechner mit einem WireGuard-Tunnel verbunden ist. Bleibt unsichtbar, bis die erste Abfrage fertig ist."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("wgIndicator")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.status = Status(UNKNOWN, [])
        self._last_name = ""  # zuletzt gesehener Tunnel, damit „getrennt“ sagt, welcher
        self._job: Job | None = None
        self._popup: WireGuardPopup | None = None
        self._stopped = False
        self._timer = QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self.refresh)
        self.clicked.connect(self.show_details)
        theme.notifier().changed.connect(self._render)
        self.setVisible(False)
        self._render()

    # --- Abfrage ---
    def start(self):
        self._stopped = False
        self.refresh()
        self._timer.start()

    def stop(self):
        self._stopped = True
        self._timer.stop()

    def set_enabled(self, enabled: bool):
        """Aus: nicht mehr abfragen und nicht anzeigen. An: abfragen; sichtbar wird die Pille mit der ersten Antwort."""
        if enabled:
            self.start()
        else:
            self.stop()
            self.setVisible(False)
            self._close_popup()

    def refresh(self):
        if self._stopped or (self._job is not None and self._job.isRunning()):
            return
        job = Job(lambda _job: wireguard.detect(), self)
        job.result.connect(self.set_status)
        self._job = job
        job.start()

    # --- Anzeige ---
    def set_status(self, status: Status):
        if self._stopped:
            return
        self.status = status
        if status.tunnels:
            self._last_name = status.tunnels[0].name
        self._render()
        self.setVisible(True)

    def text_now(self) -> str:
        return label_text(self.status, self._last_name)

    def _render(self, *_):
        icon_name, color_key = LOOK[self.status.state]
        colors = theme.colors()
        side = max(14, round(16 * theme.scale() / 100))
        self.setIcon(style.icon(icon_name, colors[color_key], size=side))
        self.setIconSize(QSize(side, side))
        self.setText(self.text_now())
        self.setToolTip(tr("Klicken für Einzelheiten"))
        self.setProperty("wgState", self.status.state)
        self.style().unpolish(self)
        self.style().polish(self)

    def _close_popup(self):
        if self._popup is not None:
            try:
                self._popup.close()
            except RuntimeError:  # schon zerstört
                pass
            self._popup = None

    def show_details(self):
        self._close_popup()
        self._popup = WireGuardPopup(self.status, self)
        self._popup.show_above(self)
