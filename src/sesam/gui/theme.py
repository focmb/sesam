"""Darstellung: Systemvorgabe, Hell oder Dunkel."""
from __future__ import annotations

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QFont

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


def apply_scale(app, percent: int) -> int:
    """Skaliert die Schrift der ganzen Oberfläche (Prozent der Systemschrift) und liefert den angewandten Wert."""
    global _scale, _base_font
    _scale = clamp_scale(percent)
    if _base_font is None:
        _base_font = QFont(app.font())
    font = QFont(_base_font)
    if _base_font.pointSizeF() > 0:
        font.setPointSizeF(_base_font.pointSizeF() * _scale / 100)
    elif _base_font.pixelSize() > 0:
        font.setPixelSize(max(1, round(_base_font.pixelSize() * _scale / 100)))
    app.setFont(font)
    notifier().changed.emit()
    return _scale
