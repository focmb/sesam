"""Darstellung: Systemvorgabe, Hell oder Dunkel."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette

MODES = ("system", "light", "dark")
LABELS = {"system": "Systemvorgabe", "light": "Hell", "dark": "Dunkel"}

_original_style: str | None = None
_applying = False
_mode = "system"
_connected = False


def dark_palette() -> QPalette:
    p = QPalette()
    window, base, text = QColor(45, 45, 48), QColor(30, 30, 30), QColor(230, 230, 230)
    accent, disabled = QColor(53, 132, 228), QColor(125, 125, 125)
    for role, color in (
        (QPalette.ColorRole.Window, window),
        (QPalette.ColorRole.WindowText, text),
        (QPalette.ColorRole.Base, base),
        (QPalette.ColorRole.AlternateBase, QColor(40, 40, 43)),
        (QPalette.ColorRole.ToolTipBase, QColor(25, 25, 25)),
        (QPalette.ColorRole.ToolTipText, text),
        (QPalette.ColorRole.Text, text),
        (QPalette.ColorRole.Button, QColor(58, 58, 62)),
        (QPalette.ColorRole.ButtonText, text),
        (QPalette.ColorRole.BrightText, QColor(255, 80, 80)),
        (QPalette.ColorRole.Link, QColor(95, 170, 255)),
        (QPalette.ColorRole.Highlight, accent),
        (QPalette.ColorRole.HighlightedText, QColor(255, 255, 255)),
        (QPalette.ColorRole.PlaceholderText, QColor(140, 140, 140)),
    ):
        p.setColor(role, color)
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
        p.setColor(QPalette.ColorGroup.Disabled, role, disabled)
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Highlight, QColor(80, 80, 84))
    return p


def system_is_dark(app) -> bool:
    scheme = getattr(Qt, "ColorScheme", None)  # ab Qt 6.5
    get = getattr(app.styleHints(), "colorScheme", None)
    return bool(scheme and get and get() == scheme.Dark)


def apply(app, mode: str) -> str:
    """Wendet die Darstellung an und liefert den tatsächlich genutzten Modus („light“ oder „dark“)."""
    global _original_style, _applying, _mode, _connected
    if _original_style is None:
        _original_style = app.style().objectName()
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
        if mode == "system" and not dark:
            app.setStyle(_original_style)  # nativer Stil bei hellem System
            app.setPalette(app.style().standardPalette())
        else:
            app.setStyle("Fusion")
            app.setPalette(dark_palette() if dark else app.style().standardPalette())
        if not _connected and hasattr(app.styleHints(), "colorSchemeChanged"):
            app.styleHints().colorSchemeChanged.connect(lambda *_: None if _applying else apply(app, _mode) if _mode == "system" else None)
            _connected = True
    finally:
        _applying = False
    return "dark" if dark else "light"
