"""Darstellung: Systemvorgabe, Hell oder Dunkel."""
from __future__ import annotations

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from ..models import FONT_SCALE_MAX, FONT_SCALE_MIN
from . import style

MODES = ("system", "light", "dark")
LABELS = {"system": "Systemvorgabe", "light": "Hell", "dark": "Dunkel"}

_applying = False
_mode = "system"
_effective = "light"
_connected = False
_scale = 100
_base_font: QFont | None = None  # Schrift des Systems vor jeder Skalierung
_expected_font: QFont | None = None  # Anwendungsschrift, die wir zuletzt selbst gesetzt haben
_setting_font = False
_font_guard_connected = False
SCALE_STEPS = (80, 90, 100, 110, 125, 150, 175, 200)


class _Notifier(QObject):
    changed = Signal()  # nach jedem Wechsel der Darstellung; Fenster färben darauf ihre Icons neu


_notifier: _Notifier | None = None


def notifier() -> QObject:
    global _notifier
    if _notifier is None:
        _notifier = _Notifier()
    return _notifier


def colors() -> dict[str, str]:
    """Farbwerte der gerade aktiven Darstellung."""
    return style.tokens(_effective)


def system_is_dark(app) -> bool:
    scheme = getattr(Qt, "ColorScheme", None)  # ab Qt 6.5
    get = getattr(app.styleHints(), "colorScheme", None)
    return bool(scheme and get and get() == scheme.Dark)


def apply(app, mode: str) -> str:
    """Wendet die Darstellung an und liefert den tatsächlich genutzten Modus („light“ oder „dark“)."""
    global _applying, _mode, _effective, _connected
    mode = mode if mode in MODES else "system"
    _mode = mode
    _applying = True
    try:
        hints = app.styleHints()
        scheme = getattr(Qt, "ColorScheme", None)
        if scheme and hasattr(hints, "setColorScheme"):  # ab Qt 6.8: Titelleiste und native Elemente mitziehen
            try:
                hints.setColorScheme({"system": scheme.Unknown, "light": scheme.Light, "dark": scheme.Dark}[mode])
            except Exception:  # noqa: BLE001
                pass
        dark = mode == "dark" or (mode == "system" and system_is_dark(app))
        _effective = "dark" if dark else "light"
        t = style.tokens(_effective)
        app.setStyle("Fusion")  # überall gleich; das Stylesheet baut darauf auf
        app.setPalette(style.palette(t))
        app.setStyleSheet(style.stylesheet(t))
        if not _connected and hasattr(app.styleHints(), "colorSchemeChanged"):
            app.styleHints().colorSchemeChanged.connect(lambda *_: None if _applying else apply(app, _mode) if _mode == "system" else None)
            _connected = True
    finally:
        _applying = False
    notifier().changed.emit()
    return _effective


def clamp_scale(percent) -> int:
    try:
        return max(FONT_SCALE_MIN, min(FONT_SCALE_MAX, int(percent)))
    except (TypeError, ValueError):
        return 100


def scale() -> int:
    return _scale


def next_scale(percent: int, direction: int) -> int:
    """Nächste Stufe über (+1) oder unter (-1) dem aktuellen Wert; am Rand bleibt der Wert stehen."""
    steps = SCALE_STEPS
    if direction > 0:
        return next((s for s in steps if s > percent), steps[-1])
    return next((s for s in reversed(steps) if s < percent), steps[0])


def scaled(font: QFont) -> QFont:
    """Kopie einer Schrift in der aktuellen Skalierung.

    Für Schriften, die ein Fenster selbst setzt (z. B. die Festschrift im Snippet-Editor): Sie erbt die
    Skalierung nicht von der Anwendungsschrift und muss deshalb mitgezogen werden.
    """
    out = QFont(font)
    if font.pointSizeF() > 0:
        out.setPointSizeF(font.pointSizeF() * _scale / 100)
    elif font.pixelSize() > 0:
        out.setPixelSize(max(1, round(font.pixelSize() * _scale / 100)))
    return out


def _same_font(a: QFont, b: QFont) -> bool:
    return a.family() == b.family() and abs(a.pointSizeF() - b.pointSizeF()) < 0.01 and a.pixelSize() == b.pixelSize()


def _on_app_font_changed(font: QFont) -> None:
    """Das System hat die Anwendungsschrift selbst gesetzt (z. B. Wayland/GNOME melden ihr Theme kurz nach dem Start)."""
    if _setting_font or _expected_font is None or _same_font(font, _expected_font):
        return
    QTimer.singleShot(0, _reapply_after_system_change)  # nicht mitten in der Änderung erneut setzen


def _reapply_after_system_change() -> None:
    global _base_font
    app = QApplication.instance()
    if app is None or _expected_font is None or _same_font(app.font(), _expected_font):
        return
    _base_font = QFont(app.font())  # die neue Systemschrift ist die neue Basis (auch bei geänderter Systemschrift)
    apply_scale(app, _scale)


def apply_scale(app, percent: int) -> int:
    """Skaliert die Schrift der ganzen Oberfläche (Prozent der Systemschrift) und liefert den angewandten Wert.

    Setzt das System die Anwendungsschrift später selbst zurück, wird die Skalierung automatisch erneut angewandt.
    """
    global _scale, _base_font, _expected_font, _setting_font, _font_guard_connected
    _scale = clamp_scale(percent)
    if _base_font is None:
        _base_font = QFont(app.font())
    font = scaled(_base_font)
    _setting_font = True
    try:
        app.setFont(font)
    finally:
        _setting_font = False
    _expected_font = QFont(app.font())
    if not _font_guard_connected and hasattr(app, "fontChanged"):
        app.fontChanged.connect(_on_app_font_changed)
        _font_guard_connected = True
    notifier().changed.emit()
    return _scale
