"""Gemeinsames Erscheinungsbild: Farbwerte, Stylesheet (QSS) und Icons für hell und dunkel."""
from __future__ import annotations

import tempfile
from pathlib import Path

from PySide6.QtCore import QByteArray, QRectF, QStandardPaths, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtSvg import QSvgRenderer

# Alle Farben an einer Stelle; das Stylesheet und die Palette werden daraus erzeugt.
TOKENS: dict[str, dict[str, str]] = {
    "light": {
        "bg": "#f4f5f8",
        "surface": "#ffffff",
        "surface_alt": "#f0f2f6",
        "border": "#dde1e8",
        "text": "#1d2230",
        "muted": "#667085",
        "accent": "#3a6cf0",
        "accent_hover": "#2f5bd6",
        "accent_text": "#ffffff",
        "accent_soft": "rgba(58, 108, 240, 0.14)",
        "sel": "#e3eafd",
        "row_hover": "#f0f0f1",
        "hover": "rgba(29, 34, 48, 0.07)",
        "pressed": "rgba(29, 34, 48, 0.13)",
        "danger": "#d92d20",
    },
    "dark": {
        "bg": "#1a1c22",
        "surface": "#22252d",
        "surface_alt": "#292d36",
        "border": "#343944",
        "text": "#e7e9ef",
        "muted": "#9aa1b1",
        "accent": "#5b8cff",
        "accent_hover": "#76a0ff",
        "accent_text": "#0f1420",
        "accent_soft": "rgba(91, 140, 255, 0.22)",
        "sel": "#2f3c5b",
        "row_hover": "#32343c",
        "hover": "rgba(255, 255, 255, 0.07)",
        "pressed": "rgba(255, 255, 255, 0.12)",
        "danger": "#f97066",
    },
}


def tokens(mode: str) -> dict[str, str]:
    return TOKENS["dark" if mode == "dark" else "light"]


def palette(t: dict[str, str]) -> QPalette:
    """Palette passend zu den Farbwerten – für alles, was das Stylesheet nicht abdeckt."""
    p = QPalette()
    text, muted = QColor(t["text"]), QColor(t["muted"])
    for role, color in (
        (QPalette.ColorRole.Window, QColor(t["bg"])),
        (QPalette.ColorRole.WindowText, text),
        (QPalette.ColorRole.Base, QColor(t["surface"])),
        (QPalette.ColorRole.AlternateBase, QColor(t["surface_alt"])),
        (QPalette.ColorRole.ToolTipBase, QColor(t["surface"])),
        (QPalette.ColorRole.ToolTipText, text),
        (QPalette.ColorRole.Text, text),
        (QPalette.ColorRole.Button, QColor(t["surface"])),
        (QPalette.ColorRole.ButtonText, text),
        (QPalette.ColorRole.BrightText, QColor(t["danger"])),
        (QPalette.ColorRole.Link, QColor(t["accent"])),
        (QPalette.ColorRole.Highlight, QColor(t["accent"])),
        (QPalette.ColorRole.HighlightedText, QColor(t["accent_text"])),
        (QPalette.ColorRole.PlaceholderText, muted),
    ):
        p.setColor(role, color)
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
        p.setColor(QPalette.ColorGroup.Disabled, role, muted)
    return p


def stylesheet(t: dict[str, str], base: Path | None = None) -> str:
    t = {**t, **_arrow_files(t, base)}
    qss = """
    QMainWindow, QDialog { background: @bg@; }
    QToolTip { background: @surface@; color: @text@; border: 1px solid @border@; border-radius: 6px; padding: 4px 8px; }

    /* Menüleiste und Menüs */
    QMenuBar { background: @bg@; color: @text@; padding: 3px 8px 0 8px; }
    QMenuBar::item { background: transparent; padding: 5px 10px; border-radius: 6px; }
    QMenuBar::item:selected, QMenuBar::item:pressed { background: @hover@; }
    QMenu { background: @surface@; color: @text@; border: 1px solid @border@; border-radius: 8px; padding: 6px; }
    QMenu::item { padding: 6px 24px 6px 14px; border-radius: 6px; }
    QMenu::item:selected { background: @accent_soft@; color: @text@; }
    QMenu::item:disabled { color: @muted@; }
    QMenu::separator { height: 1px; background: @border@; margin: 5px 8px; }

    /* Seitenleiste mit den Hauptaktionen */
    QWidget#sidebar { background: transparent; }
    QFrame#sideSep { background: @border@; border: none; }
    QPushButton#side { background: transparent; color: @text@; border: 1px solid transparent; border-radius: 8px;
        padding: 7px 12px; text-align: left; }
    QPushButton#side:hover { background: @hover@; border-color: transparent; }
    QPushButton#side:pressed { background: @pressed@; }
    QPushButton#side:focus { border-color: @accent@; }
    QPushButton#side:disabled { background: transparent; color: @muted@; border-color: transparent; }
    QPushButton#side[primary="true"] { background: @accent@; color: @accent_text@; font-weight: 600; }
    QPushButton#side[primary="true"]:hover { background: @accent_hover@; }
    QPushButton#side[primary="true"]:disabled { background: @hover@; color: @muted@; }

    /* Eingabefelder */
    QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextEdit {
        background: @surface@; color: @text@; border: 1px solid @border@; border-radius: 8px;
        padding: 4px 10px; selection-background-color: @accent@; selection-color: @accent_text@; }
    QLineEdit:hover, QSpinBox:hover, QComboBox:hover, QPlainTextEdit:hover, QTextEdit:hover { border-color: @muted@; }
    QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus, QTextEdit:focus {
        border: 1px solid @accent@; }
    QLineEdit:disabled, QSpinBox:disabled, QComboBox:disabled, QPlainTextEdit:disabled, QTextEdit:disabled {
        background: @surface_alt@; color: @muted@; }
    QComboBox::drop-down { subcontrol-origin: padding; subcontrol-position: center right; width: 28px; border: none; }
    QComboBox::down-arrow { image: url("@arrow_down@"); }
    QSpinBox::up-button, QDoubleSpinBox::up-button { subcontrol-origin: border; subcontrol-position: top right;
        width: 24px; border: none; background: transparent; }
    QSpinBox::down-button, QDoubleSpinBox::down-button { subcontrol-origin: border; subcontrol-position: bottom right;
        width: 24px; border: none; background: transparent; }
    QSpinBox::up-arrow, QDoubleSpinBox::up-arrow { image: url("@arrow_up@"); }
    QSpinBox::down-arrow, QDoubleSpinBox::down-arrow { image: url("@arrow_down@"); }
    QComboBox QAbstractItemView { background: @surface@; color: @text@; border: 1px solid @border@; border-radius: 8px;
        padding: 4px; outline: 0; selection-background-color: @accent_soft@; selection-color: @text@; }

    /* Schaltflächen */
    QPushButton { background: @surface@; color: @text@; border: 1px solid @border@; border-radius: 8px;
        padding: 6px 12px; min-height: 18px; }
    QPushButton::menu-indicator { image: none; width: 0; }
    QPushButton:hover { background: @surface_alt@; border-color: @muted@; }
    QPushButton:pressed { background: @pressed@; }
    QPushButton:disabled { color: @muted@; background: @surface_alt@; border-color: @border@; }
    QPushButton:default { background: @accent@; color: @accent_text@; border-color: @accent@; font-weight: 600; }
    QPushButton:default:hover { background: @accent_hover@; border-color: @accent_hover@; }
    QPushButton:default:disabled { background: @hover@; color: @muted@; border-color: @border@; }

    /* Baum und Listen */
    QTreeView, QListView, QListWidget, QTableView {
        background: @surface@; color: @text@; border: 1px solid @border@; border-radius: 10px;
        outline: 0; padding: 4px; selection-background-color: @sel@; selection-color: @text@; }
    QTreeView::item, QListView::item { padding: 6px 4px; border: none; }
    QTreeView::item:hover, QListView::item:hover { background: @row_hover@; }
    QTreeView::branch { background: transparent; }
    QTreeView::branch:hover { background: @row_hover@; }
    QTreeView::branch:selected { background: @sel@; }
    QTreeView::branch:closed:has-children { image: url("@arrow_right@"); }
    QTreeView::branch:open:has-children { image: url("@arrow_down@"); }
    QTreeView::item:selected, QListView::item:selected,
    QTreeView::item:selected:active, QListView::item:selected:active { background: @sel@; color: @text@; }
    QHeaderView { background: transparent; }
    QHeaderView::section { background: transparent; color: @muted@; border: none; border-bottom: 1px solid @border@;
        padding: 8px 10px; font-weight: 600; }

    /* Rahmen, Gruppen, Reiter */
    QGroupBox { border: 1px solid @border@; border-radius: 10px; margin-top: 14px; padding: 14px 10px 10px 10px; }
    QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 6px; color: @muted@; font-weight: 600; }
    QTabWidget::pane { border: 1px solid @border@; border-radius: 10px; background: @surface@; top: -1px; }
    QTabBar::tab { background: transparent; color: @muted@; padding: 8px 14px; border: none; border-bottom: 2px solid transparent; }
    QTabBar::tab:selected { color: @text@; border-bottom: 2px solid @accent@; }
    QTabBar::tab:hover:!selected { color: @text@; }

    /* Statuszeile */
    QStatusBar { background: transparent; color: @muted@; }
    QStatusBar::item { border: none; }
    QStatusBar QLabel { color: @muted@; }

    /* Schmale Scrollleisten */
    QScrollBar:vertical { background: transparent; width: 12px; margin: 2px; }
    QScrollBar:horizontal { background: transparent; height: 12px; margin: 2px; }
    QScrollBar::handle:vertical { background: @border@; border-radius: 4px; min-height: 28px; }
    QScrollBar::handle:horizontal { background: @border@; border-radius: 4px; min-width: 28px; }
    QScrollBar::handle:hover { background: @muted@; }
    QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; background: none; border: none; }
    QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
    """
    for key, value in t.items():
        qss = qss.replace(f"@{key}@", value)
    return qss


def _arrow_files(t: dict[str, str], base: Path | None = None) -> dict[str, str]:
    """Schreibt die Auf-/Zuklapp-Pfeile des Baums als PNG (Stylesheets können nur Dateien einbinden)."""
    if base is None:
        base = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation) or tempfile.gettempdir())
    folder = base / "sesam-ui"
    out = {"arrow_right": "none", "arrow_down": "none", "arrow_up": "none"}
    try:
        folder.mkdir(parents=True, exist_ok=True)
        key = t["muted"].lstrip("#")
        for token, name in (("arrow_right", "chevron_right"), ("arrow_down", "chevron_down"), ("arrow_up", "chevron_up")):
            base = folder / f"{name}-{key}.png"
            for scale, path in ((1, base), (2, base.with_name(f"{base.stem}@2x.png"))):
                pix = _pixmap(name, t["muted"], 12 * scale)
                pix.setDevicePixelRatio(1.0)
                pix = pix.scaled(12 * scale, 12 * scale, Qt.AspectRatioMode.IgnoreAspectRatio,
                                 Qt.TransformationMode.SmoothTransformation)
                pix.save(str(path), "PNG")
            out[token] = base.as_posix()
    except OSError:
        pass  # ohne Pfeil-Dateien bleibt der Baum bedienbar (Doppelklick/Pfeiltasten)
    return out


# --- Icons: eigene, einfache Linienzeichnungen (24 × 24) ------------------------------------
_ICONS = {
    "connect": '<rect x="3" y="4" width="18" height="16" rx="2.5"/><path d="M7.5 9.5l3 2.5-3 2.5M13 15h4"/>',
    "add": '<path d="M12 5v14M5 12h14"/>',
    "folder": '<path d="M3 7.5A2.5 2.5 0 0 1 5.5 5h3.6l2 2.2h7.4A2.5 2.5 0 0 1 21 9.7v7.8a2.5 2.5 0 0 1-2.5 2.5h-13A2.5 2.5 0 0 1 3 17.5z"/>',
    "folder_add": '<path d="M3 7.5A2.5 2.5 0 0 1 5.5 5h3.6l2 2.2h7.4A2.5 2.5 0 0 1 21 9.7v7.8a2.5 2.5 0 0 1-2.5 2.5h-13A2.5 2.5 0 0 1 3 17.5z"/><path d="M12 11v5M9.5 13.5h5"/>',
    "edit": '<path d="M4 20h4L19.5 8.5a2.1 2.1 0 0 0-3-3L5 17z"/><path d="M14 7l3 3"/>',
    "copy": '<rect x="8.5" y="8.5" width="11.5" height="11.5" rx="2.5"/><path d="M15.5 8.5V6.5A2.5 2.5 0 0 0 13 4H6.5A2.5 2.5 0 0 0 4 6.5V13a2.5 2.5 0 0 0 2.5 2.5h2"/>',
    "trash": '<path d="M4 7h16M9.5 7V4.8h5V7M6.2 7l.9 12.2h9.8L17.8 7M10 11v5M14 11v5"/>',
    "sync": '<path d="M20 11A8 8 0 0 0 6 6.8M4 5v4.2h4.2"/><path d="M4 13a8 8 0 0 0 14 4.2M20 19v-4.2h-4.2"/>',
    "user": '<circle cx="12" cy="8" r="4"/><path d="M4.5 20c.6-3.8 3.8-6 7.5-6s6.900 2.200 7.500 6"/>',
    "sliders": '<path d="M4 7h9M19 7h1M4 17h1M11 17h9"/><circle cx="16" cy="7" r="2.200"/><circle cx="8" cy="17" r="2.200"/>',
    "server": '<rect x="4" y="4" width="16" height="6.500" rx="2"/><rect x="4" y="13.500" width="16" height="6.500" rx="2"/><path d="M8 7.250h.01M8 16.750h.01"/>',
    "chevron_right": '<path d="M9 6l6 6-6 6"/>',
    "chevron_up": '<path d="M6 15l6-6 6 6"/>',
    "chevron_down": '<path d="M6 9l6 6 6-6"/>',
    "close": '<path d="M7 7l10 10M17 7L7 17"/>',
    "search": '<circle cx="11" cy="11" r="6.500"/><path d="M16 16l4 4"/>',
}


def icon(name: str, color: str, disabled_color: str | None = None, size: int = 18) -> QIcon:
    """Zeichnet ein Linien-Icon in der gewünschten Farbe (scharf auch auf HiDPI-Bildschirmen)."""
    result = QIcon()
    for mode, col in ((QIcon.Mode.Normal, color), (QIcon.Mode.Disabled, disabled_color or color)):
        result.addPixmap(_pixmap(name, col, size), mode)
    return result


def _pixmap(name: str, color: str, size: int) -> QPixmap:
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="%s" '
        'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">%s</svg>' % (color, _ICONS[name])
    )
    ratio = 2.0  # genug für HiDPI; Qt skaliert bei Bedarf herunter
    pix = QPixmap(int(size * ratio), int(size * ratio))
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    QSvgRenderer(QByteArray(svg.encode())).render(painter, QRectF(0, 0, size * ratio, size * ratio))
    painter.end()
    pix.setDevicePixelRatio(ratio)
    return pix


# Programm-Icon (Fenster, Taskleiste): das Tor aus „Sesam, öffne dich“ – ein goldenes Schlüsselloch mit Terminal-Prompt auf einer Platine. Identisch mit packaging/linux/sesam.svg – ein Test stellt das sicher.
APP_ICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#2b3350"/>
      <stop offset="1" stop-color="#171b2c"/>
    </linearGradient>
    <linearGradient id="gold" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#ffe48a"/>
      <stop offset="0.45" stop-color="#f3b73c"/>
      <stop offset="1" stop-color="#b9771c"/>
    </linearGradient>
    <radialGradient id="halo" cx="0.5" cy="0.5" r="0.5">
      <stop offset="0" stop-color="#ffbe3d" stop-opacity="0.55"/>
      <stop offset="1" stop-color="#ffbe3d" stop-opacity="0"/>
    </radialGradient>
    <radialGradient id="hole" cx="0.5" cy="0.4" r="0.7">
      <stop offset="0" stop-color="#2a1d10"/>
      <stop offset="1" stop-color="#0d0f16"/>
    </radialGradient>
    <clipPath id="clip"><rect x="4" y="4" width="120" height="120" rx="26"/></clipPath>
  </defs>
  <rect x="4" y="4" width="120" height="120" rx="26" fill="url(#bg)"/>
  <g clip-path="url(#clip)">
    <g fill="none" stroke-width="0.9" stroke-linecap="round" stroke-linejoin="round" opacity="0.85"><path d="M60.0 54.0 L44.0 54.0 L31.0 41.0 L12.0 41.0" stroke="#c99a3a"/><path d="M68.0 54.0 L84.0 54.0 L97.0 41.0 L116.0 41.0" stroke="#c99a3a"/><path d="M60.0 49.6 L40.9 49.6 L27.9 36.6 L12.0 36.6" stroke="#c99a3a"/><path d="M68.0 49.6 L87.1 49.6 L100.1 36.6 L116.0 36.6" stroke="#c99a3a"/><path d="M60.0 45.2 L37.8 45.2 L24.8 32.2 L12.0 32.2" stroke="#4cc3ff"/><path d="M68.0 45.2 L90.2 45.2 L103.2 32.2 L116.0 32.2" stroke="#c99a3a"/><path d="M60.0 40.8 L34.7 40.8 L21.7 27.8 L12.0 27.8" stroke="#c99a3a"/><path d="M68.0 40.8 L93.3 40.8 L106.3 27.8 L116.0 27.8" stroke="#c99a3a"/><path d="M60.0 36.4 L31.6 36.4 L18.6 23.4 L12.0 23.4" stroke="#c99a3a"/><path d="M68.0 36.4 L96.4 36.4 L109.4 23.4 L116.0 23.4" stroke="#c99a3a"/><path d="M60.0 32.0 L28.5 32.0 L15.5 19.0 L12.0 19.0" stroke="#c99a3a"/><path d="M68.0 32.0 L99.5 32.0 L112.5 19.0 L116.0 19.0" stroke="#c99a3a"/><path d="M60.0 74.0 L28.5 74.0 L15.5 87.0 L12.0 87.0" stroke="#c99a3a"/><path d="M68.0 74.0 L99.5 74.0 L112.5 87.0 L116.0 87.0" stroke="#c99a3a"/><path d="M60.0 78.4 L31.6 78.4 L18.6 91.4 L12.0 91.4" stroke="#c99a3a"/><path d="M68.0 78.4 L96.4 78.4 L109.4 91.4 L116.0 91.4" stroke="#c99a3a"/><path d="M60.0 82.8 L34.7 82.8 L21.7 95.8 L12.0 95.8" stroke="#c99a3a"/><path d="M68.0 82.8 L93.3 82.8 L106.3 95.8 L116.0 95.8" stroke="#c99a3a"/><path d="M60.0 87.2 L37.8 87.2 L24.8 100.2 L12.0 100.2" stroke="#c99a3a"/><path d="M68.0 87.2 L90.2 87.2 L103.2 100.2 L116.0 100.2" stroke="#c99a3a"/><path d="M60.0 91.6 L40.9 91.6 L27.9 104.6 L12.0 104.6" stroke="#c99a3a"/><path d="M68.0 91.6 L87.1 91.6 L100.1 104.6 L116.0 104.6" stroke="#c99a3a"/><path d="M60.0 96.0 L44.0 96.0 L31.0 109.0 L12.0 109.0" stroke="#c99a3a"/><path d="M68.0 96.0 L84.0 96.0 L97.0 109.0 L116.0 109.0" stroke="#c99a3a"/><path d="M30.0 22.0 L22.0 22.0 L17.0 27.0" stroke="#c99a3a"/><path d="M98.0 22.0 L106.0 22.0 L111.0 27.0" stroke="#c99a3a"/><path d="M34.0 17.0 L34.0 12.0" stroke="#c99a3a"/><path d="M94.0 17.0 L94.0 12.0" stroke="#c99a3a"/><path d="M26.0 108.0 L20.0 108.0 L16.0 104.0" stroke="#c99a3a"/><path d="M102.0 108.0 L108.0 108.0 L112.0 104.0" stroke="#c99a3a"/><path d="M36.0 115.0 L36.0 119.0" stroke="#c99a3a"/><path d="M92.0 115.0 L92.0 119.0" stroke="#c99a3a"/></g>
    <g fill="#10131c" stroke="#c99a3a" stroke-width="0.7"><circle cx="12.0" cy="41.0" r="1.4"/><circle cx="116.0" cy="41.0" r="1.4"/><circle cx="12.0" cy="36.6" r="1.4"/><circle cx="116.0" cy="36.6" r="1.4"/><circle cx="12.0" cy="32.2" r="1.4"/><circle cx="116.0" cy="32.2" r="1.4"/><circle cx="12.0" cy="27.8" r="1.4"/><circle cx="116.0" cy="27.8" r="1.4"/><circle cx="12.0" cy="23.4" r="1.4"/><circle cx="116.0" cy="23.4" r="1.4"/><circle cx="12.0" cy="19.0" r="1.4"/><circle cx="116.0" cy="19.0" r="1.4"/><circle cx="12.0" cy="87.0" r="1.4"/><circle cx="116.0" cy="87.0" r="1.4"/><circle cx="12.0" cy="91.4" r="1.4"/><circle cx="116.0" cy="91.4" r="1.4"/><circle cx="12.0" cy="95.8" r="1.4"/><circle cx="116.0" cy="95.8" r="1.4"/><circle cx="12.0" cy="100.2" r="1.4"/><circle cx="116.0" cy="100.2" r="1.4"/><circle cx="12.0" cy="104.6" r="1.4"/><circle cx="116.0" cy="104.6" r="1.4"/><circle cx="12.0" cy="109.0" r="1.4"/><circle cx="116.0" cy="109.0" r="1.4"/><circle cx="17.0" cy="27.0" r="1.4"/><circle cx="111.0" cy="27.0" r="1.4"/><circle cx="34.0" cy="12.0" r="1.4"/><circle cx="94.0" cy="12.0" r="1.4"/><circle cx="16.0" cy="104.0" r="1.4"/><circle cx="112.0" cy="104.0" r="1.4"/><circle cx="36.0" cy="119.0" r="1.4"/><circle cx="92.0" cy="119.0" r="1.4"/></g>
    <rect x="12" y="38" width="5" height="8" rx="0.8" fill="#10131c" stroke="#3a4257" stroke-width="0.5"/><rect x="10.4" y="39.0" width="1.6" height="1" fill="#c99a3a"/><rect x="17" y="39.0" width="1.6" height="1" fill="#c99a3a"/><rect x="10.4" y="41.6" width="1.6" height="1" fill="#c99a3a"/><rect x="17" y="41.6" width="1.6" height="1" fill="#c99a3a"/><rect x="111" y="86" width="5" height="8" rx="0.8" fill="#10131c" stroke="#3a4257" stroke-width="0.5"/><rect x="109.4" y="87.0" width="1.6" height="1" fill="#c99a3a"/><rect x="116" y="87.0" width="1.6" height="1" fill="#c99a3a"/><rect x="109.4" y="89.6" width="1.6" height="1" fill="#c99a3a"/><rect x="116" y="89.6" width="1.6" height="1" fill="#c99a3a"/>
    <ellipse cx="64" cy="66" rx="50" ry="56" fill="url(#halo)"/>
  </g>
  <rect x="4.5" y="4.5" width="119" height="119" rx="25.5" fill="none" stroke="#0b0d15" stroke-width="1"/>
  <path d="M40 113L50 72A28 28 0 1 1 78 72L88 113Z" fill="url(#gold)" stroke="#8a560f" stroke-width="1.6" stroke-linejoin="round"/>
  <path d="M43 110.6L52.5 71.5A25.6 25.6 0 1 1 75.5 71.5L85 110.6Z" fill="none" stroke="#fff0b0" stroke-width="0.9" stroke-linejoin="round" opacity="0.8"/>
  <path d="M54.5 66A20 20 0 1 1 73.5 66L79 92H49Z" fill="url(#hole)" stroke="#ffb73a" stroke-width="1.6" stroke-linejoin="round"/>
  <path d="M55.5 42l8 5.5-8 5.5" fill="none" stroke="#ffb73a" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/>
  <path d="M66.5 55h9" fill="none" stroke="#ffb73a" stroke-width="2.6" stroke-linecap="round"/>
  <g fill="none" stroke="#7a4a0c" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M49.8 98.6 L43.8 98.6 L43.8 102.6 L49.8 102.6 L49.8 106.6 L43.8 106.6"/><path d="M58.4 98.6 L52.4 98.6 L52.4 106.6 L58.4 106.6"/><path d="M52.4 102.6 L57.4 102.6"/><path d="M67.0 98.6 L61.0 98.6 L61.0 102.6 L67.0 102.6 L67.0 106.6 L61.0 106.6"/><path d="M69.6 106.6 L72.6 98.6 L75.6 106.6"/><path d="M70.9 103.6 L74.3 103.6"/><path d="M78.2 106.6 L78.2 98.6 L81.2 103.0 L84.2 98.6 L84.2 106.6"/></g>
</svg>
"""


def app_icon() -> QIcon:
    result = QIcon()
    renderer = QSvgRenderer(QByteArray(APP_ICON_SVG.encode()))
    for size in (16, 24, 32, 48, 64, 128, 256):
        pix = QPixmap(size, size)
        pix.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        renderer.render(painter, QRectF(0, 0, size, size))
        painter.end()
        result.addPixmap(pix)
    return result
