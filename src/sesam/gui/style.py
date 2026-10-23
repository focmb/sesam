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
    QPushButton#seg { background: transparent; border: 1px solid @border@; border-radius: 0; padding: 6px 0; }
    QPushButton#seg[pos="first"] { border-top-left-radius: 8px; border-bottom-left-radius: 8px; }
    QPushButton#seg[pos="last"] { border-top-right-radius: 8px; border-bottom-right-radius: 8px; }
    QPushButton#seg:hover { background: @hover@; }
    QPushButton#seg:checked { background: @accent@; border-color: @accent@; }
    QPushButton#seg:focus { border-color: @accent@; }
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

    /* Werkzeugknöpfe (Dateibrowser) und Fortschrittsbalken */
    QToolButton { background: transparent; border: 1px solid transparent; border-radius: 8px; padding: 5px; }
    QToolButton:hover { background: @hover@; }
    QToolButton:pressed { background: @pressed@; }
    QToolButton:disabled { color: @muted@; }
    QProgressBar { background: @surface@; color: @text@; border: 1px solid @border@; border-radius: 8px; text-align: center; min-height: 18px; }
    QProgressBar::chunk { background: @accent_soft@; border-radius: 7px; }

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
    "settings": '<path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/>',
    "gear": '<circle cx="12" cy="12" r="3"/><path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M18.4 5.6l-2.1 2.1M7.7 16.3l-2.1 2.1"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6l1.4 1.4M17 17l1.4 1.4M18.4 5.6L17 7M7 17l-1.4 1.4"/>',
    "moon": '<path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z"/>',
    "monitor": '<rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/>',
    "snippet": '<path d="M8 8l-4 4 4 4M16 8l4 4-4 4M13.5 6l-3 12"/>',
    "contrast": '<circle cx="12" cy="12" r="8.5"/><path d="M12 3.5a8.5 8.5 0 0 1 0 17z"/>',
    "font_size": '<path d="M3.5 19l6-14 6 14M6 14h7M19 6v6M16 9h6"/>',
    "key": '<circle cx="8" cy="15" r="3.5"/><path d="M10.5 12.5L20 3M16 7l3 3M13.5 9.5l2 2"/>',
    "folder": '<path d="M3 7.5A2.5 2.5 0 0 1 5.5 5h3.6l2 2.2h7.4A2.5 2.5 0 0 1 21 9.7v7.8a2.5 2.5 0 0 1-2.5 2.5h-13A2.5 2.5 0 0 1 3 17.5z"/>',
    "folder_add": '<path d="M3 7.5A2.5 2.5 0 0 1 5.5 5h3.6l2 2.2h7.4A2.5 2.5 0 0 1 21 9.7v7.8a2.5 2.5 0 0 1-2.5 2.5h-13A2.5 2.5 0 0 1 3 17.5z"/><path d="M12 11v5M9.5 13.5h5"/>',
    "edit": '<path d="M4 20h4L19.5 8.5a2.1 2.1 0 0 0-3-3L5 17z"/><path d="M14 7l3 3"/>',
    "copy": '<rect x="8.5" y="8.5" width="11.5" height="11.5" rx="2.5"/><path d="M15.5 8.5V6.5A2.5 2.5 0 0 0 13 4H6.5A2.5 2.5 0 0 0 4 6.5V13a2.5 2.5 0 0 0 2.5 2.5h2"/>',
    "trash": '<path d="M4 7h16M9.5 7V4.8h5V7M6.2 7l.9 12.2h9.8L17.8 7M10 11v5M14 11v5"/>',
    "sync": '<path d="M20 11A8 8 0 0 0 6 6.8M4 5v4.2h4.2"/><path d="M4 13a8 8 0 0 0 14 4.2M20 19v-4.2h-4.2"/>',
    "user": '<circle cx="12" cy="8" r="4"/><path d="M4.5 20c.6-3.8 3.8-6 7.5-6s6.900 2.200 7.500 6"/>',
    "sliders": '<path d="M4 7h9M19 7h1M4 17h1M11 17h9"/><circle cx="16" cy="7" r="2.200"/><circle cx="8" cy="17" r="2.200"/>',
    "box": '<path d="M12 3l8 4.500v9L12 21l-8-4.500v-9z"/><path d="M4 7.500l8 4.500 8-4.500M12 12v9"/>',
    "server": '<rect x="4" y="4" width="16" height="6.500" rx="2"/><rect x="4" y="13.500" width="16" height="6.500" rx="2"/><path d="M8 7.250h.01M8 16.750h.01"/>',
    "chevron_right": '<path d="M9 6l6 6-6 6"/>',
    "chevron_up": '<path d="M6 15l6-6 6 6"/>',
    "chevron_down": '<path d="M6 9l6 6 6-6"/>',
    "close": '<path d="M7 7l10 10M17 7L7 17"/>',
    "file": '<path d="M7 3.500h7l4.500 4.500v12A1.500 1.500 0 0 1 17 21.500H7A1.500 1.500 0 0 1 5.500 20V5A1.500 1.500 0 0 1 7 3.500z"/><path d="M14 3.500V8h4.500"/>',
    "arrow_up": '<path d="M12 19V6M6 11.5l6-6 6 6"/>',
    "home": '<path d="M4 11l8-6.5 8 6.5M6.5 9.5V19h11V9.5"/>',
    "transfer": '<path d="M8 20V6M4.500 9.500L8 6l3.500 3.500"/><path d="M16 4v14M12.500 14.500L16 18l3.500-3.500"/>',
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


# Programm-Icon (Fenster, Taskleiste): ein goldenes Schlüsselloch mit Terminal-Prompt auf dunkler Kachel, darunter der Schriftzug (als Pfad, daher ohne Schriftart). Identisch mit packaging/linux/sesam.svg – ein Test stellt das sicher.
APP_ICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128">
  <rect x="4" y="4" width="120" height="120" rx="28" fill="#263238"/>
  <g transform="translate(64 15) scale(0.72) translate(-64 -20)">
    <path d="M64 20a30 30 0 0 0-15 56L43 108h42l-6-32A30 30 0 0 0 64 20z" fill="#FFB300"/>
    <path d="M54 40l11 10-11 10" fill="none" stroke="#263238" stroke-width="7" stroke-linecap="round" stroke-linejoin="round"/>
    <path d="M70 62h14" fill="none" stroke="#263238" stroke-width="7" stroke-linecap="round"/>
  </g>
  <path d="M30.49 107.22C34.14 107.22 36.28 105.46 36.28 102.73C36.28 100.21 34.33 98.93 31.8 98.34L30.44 98.01C29.18 97.71 28.05 97.23 28.05 96.12C28.05 95.11 28.93 94.39 30.5 94.39C32.05 94.39 33 95.08 33.11 96.28H36.08C36.03 93.64 33.86 91.8 30.54 91.8C27.27 91.8 24.9 93.61 24.9 96.32C24.9 98.5 26.42 99.78 28.95 100.4L30.61 100.81C32.24 101.21 33.19 101.68 33.19 102.71C33.19 103.85 32.11 104.62 30.46 104.62C28.8 104.62 27.59 103.87 27.48 102.35H24.49C24.59 105.54 26.89 107.22 30.49 107.22ZM40.15 107H50.2V104.45H43.22V100.68H49.66V98.17H43.22V94.55H50.18V92H40.15ZM59.82 107.22C63.46 107.22 65.61 105.46 65.61 102.73C65.61 100.21 63.65 98.93 61.13 98.34L59.77 98.01C58.5 97.71 57.37 97.23 57.37 96.12C57.37 95.11 58.26 94.39 59.83 94.39C61.37 94.39 62.32 95.08 62.43 96.28H65.4C65.35 93.64 63.18 91.8 59.86 91.8C56.6 91.8 54.22 93.61 54.22 96.32C54.22 98.5 55.74 99.78 58.28 100.4L59.93 100.81C61.56 101.21 62.52 101.68 62.52 102.71C62.52 103.85 61.43 104.62 59.79 104.62C58.13 104.62 56.91 103.87 56.81 102.35H53.82C53.91 105.54 56.21 107.22 59.82 107.22ZM68.6 107H72L73.11 103.52H78.44L79.6 107H83.01L77.76 92H73.73ZM73.88 101.11 74.31 99.76C74.77 98.25 75.21 96.47 75.71 94.41C76.23 96.45 76.71 98.22 77.19 99.76L77.64 101.11ZM86.51 107H89.56V99.92C89.56 98.8 89.48 96.59 89.45 94.77C89.99 96.78 90.59 98.89 90.94 99.92L93.47 107H96.06L98.54 99.92C98.89 98.86 99.53 96.67 100.03 94.65C100 96.54 99.92 98.8 99.92 99.92V107H103.01V92H98.28L95.88 98.89C95.58 99.84 95.12 101.85 94.78 103.47C94.42 101.84 93.96 99.83 93.66 98.89L91.22 92H86.51Z" fill="#FFFFFF"/>
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
