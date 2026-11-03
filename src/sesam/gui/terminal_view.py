"""Integriertes Terminal: ein Qt-Widget, das die Ausgabe eines Prozesses im Pseudoterminal mit ``pyte`` auswertet und zeichnet."""
from __future__ import annotations

import copy
from collections import defaultdict, deque

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QClipboard, QColor, QFont, QFontDatabase, QFontMetricsF, QGuiApplication, QKeyEvent, QPainter, QPen,
)
from PySide6.QtWidgets import QMenu, QWidget

from .. import pty_session
from ..i18n import tr

try:
    import pyte
except ImportError:  # wird beim Öffnen gemeldet (siehe ``available``)
    pyte = None

SCROLLBACK_LINES = 10000
PADDING = 6
ALT_SCREEN_MODES = (47, 1047, 1049)
BRACKETED_PASTE = 2004 << 5  # private Modi sind in pyte um 5 Bit verschoben
APP_CURSOR_KEYS = 1 << 5  # DECCKM

_ANSI = ("black", "red", "green", "brown", "blue", "magenta", "cyan", "white")
SCHEMES = {
    "dark": {
        "bg": "#1b1d23", "fg": "#d5d8df", "cursor": "#e8a317", "selection": "#3b4a6b",
        "ansi": ["#2b2e37", "#e06c75", "#8fc07a", "#e5c07b", "#61afef", "#c678dd", "#56b6c2", "#abb2bf",
                 "#5c6370", "#ff7b86", "#a6d98f", "#f0d28a", "#7cc0ff", "#d78cf0", "#6fd0dc", "#ffffff"],
    },
    "light": {
        "bg": "#fbfbfc", "fg": "#2b2f36", "cursor": "#b8780a", "selection": "#bcd3f5",
        "ansi": ["#2b2f36", "#c4332f", "#2e7d32", "#9a6700", "#1f5fbf", "#8e3bb0", "#0e7c86", "#7b8190",
                 "#5c6370", "#e5484d", "#3d9a42", "#b8860b", "#3b82e0", "#a855c9", "#1596a2", "#4b5060"],
    },
}


def available() -> bool:
    return pyte is not None


if pyte is not None:

    class _Screen(pyte.Screen):
        """``pyte.Screen`` mit Scrollback, Alternativbildschirm (vim, htop, less …) und Antworten an den Prozess."""

        def __init__(self, columns: int, lines: int, reply):
            super().__init__(columns, lines)
            self.scrollback: deque[list] = deque(maxlen=SCROLLBACK_LINES)
            self.scrolled = 0  # wie viele Zeilen insgesamt in den Scrollback gewandert sind
            self._reply = reply
            self._main: tuple | None = None  # (Puffer, Cursor) des Hauptbildschirms, solange der Alternativbildschirm aktiv ist

        @property
        def in_alt(self) -> bool:
            return self._main is not None

        def write_process_input(self, data: str) -> None:
            self._reply(data)

        def index(self) -> None:
            top, bottom = self.margins or (0, self.lines - 1)
            if self.cursor.y == bottom and top == 0 and self._main is None:
                row = self.buffer[0]
                self.scrollback.append([row[x] for x in range(self.columns)])
                self.scrolled += 1
            super().index()

        def set_mode(self, *modes, **kwargs):
            if kwargs.get("private") and any(m in ALT_SCREEN_MODES for m in modes):
                self._enter_alt()
                modes = tuple(m for m in modes if m not in ALT_SCREEN_MODES)
            super().set_mode(*modes, **kwargs)

        def reset_mode(self, *modes, **kwargs):
            if kwargs.get("private") and any(m in ALT_SCREEN_MODES for m in modes):
                self._leave_alt()
                modes = tuple(m for m in modes if m not in ALT_SCREEN_MODES)
            super().reset_mode(*modes, **kwargs)

        def _enter_alt(self):
            if self._main is not None:
                return
            self._main = (self.buffer, copy.copy(self.cursor))
            self.buffer = defaultdict(self.buffer.default_factory)
            self.dirty.update(range(self.lines))

        def _leave_alt(self):
            if self._main is None:
                return
            self.buffer, cursor = self._main
            self._main = None
            self.cursor = cursor
            self.dirty.update(range(self.lines))


_KEYS = {
    Qt.Key.Key_Return: b"\r", Qt.Key.Key_Enter: b"\r", Qt.Key.Key_Backspace: b"\x7f", Qt.Key.Key_Tab: b"\t",
    Qt.Key.Key_Backtab: b"\x1b[Z", Qt.Key.Key_Escape: b"\x1b",
}
_CURSOR = {Qt.Key.Key_Up: "A", Qt.Key.Key_Down: "B", Qt.Key.Key_Right: "C", Qt.Key.Key_Left: "D", Qt.Key.Key_Home: "H", Qt.Key.Key_End: "F"}
_TILDE = {Qt.Key.Key_Insert: 2, Qt.Key.Key_Delete: 3, Qt.Key.Key_PageUp: 5, Qt.Key.Key_PageDown: 6}
_FKEYS = {
    Qt.Key.Key_F1: "\x1bOP", Qt.Key.Key_F2: "\x1bOQ", Qt.Key.Key_F3: "\x1bOR", Qt.Key.Key_F4: "\x1bOS",
    Qt.Key.Key_F5: "\x1b[15~", Qt.Key.Key_F6: "\x1b[17~", Qt.Key.Key_F7: "\x1b[18~", Qt.Key.Key_F8: "\x1b[19~",
    Qt.Key.Key_F9: "\x1b[20~", Qt.Key.Key_F10: "\x1b[21~", Qt.Key.Key_F11: "\x1b[23~", Qt.Key.Key_F12: "\x1b[24~",
}
_CTRL_SYMBOLS = {
    Qt.Key.Key_Space: b"\x00", Qt.Key.Key_BracketLeft: b"\x1b", Qt.Key.Key_Backslash: b"\x1c",
    Qt.Key.Key_BracketRight: b"\x1d", Qt.Key.Key_AsciiCircum: b"\x1e", Qt.Key.Key_Underscore: b"\x1f",
    Qt.Key.Key_Slash: b"\x1f", Qt.Key.Key_2: b"\x00",
}
Mod = Qt.KeyboardModifier


def key_bytes(key: int, text: str, mods, app_cursor: bool) -> bytes | None:
    """Bytes, die eine Taste an das Terminal schickt; ``None`` wenn die Taste nichts sendet."""
    ctrl, alt, shift = bool(mods & Mod.ControlModifier), bool(mods & Mod.AltModifier), bool(mods & Mod.ShiftModifier)
    xmod = 1 + (1 if shift else 0) + (2 if alt else 0) + (4 if ctrl else 0)
    if key in _CURSOR:
        letter = _CURSOR[key]
        if xmod > 1:
            return f"\x1b[1;{xmod}{letter}".encode()
        return (f"\x1bO{letter}" if app_cursor else f"\x1b[{letter}").encode()
    if key in _TILDE:
        return (f"\x1b[{_TILDE[key]};{xmod}~" if xmod > 1 else f"\x1b[{_TILDE[key]}~").encode()
    if key in _FKEYS:
        return _FKEYS[key].encode()
    if key in _KEYS:
        data = _KEYS[key]
        return b"\x1b" + data if alt and data != b"\x1b[Z" else data
    if ctrl and not alt:
        if Qt.Key.Key_A <= key <= Qt.Key.Key_Z:
            return bytes([key - Qt.Key.Key_A + 1])
        if key in _CTRL_SYMBOLS:
            return _CTRL_SYMBOLS[key]
    if text and text[0] >= " " and text[0] != "\x7f":
        data = text.encode("utf-8")
        return b"\x1b" + data if alt and not ctrl else data  # Alt+Taste = Escape-Präfix; Strg+Alt (AltGr) liefert das Zeichen selbst
    return None


class TerminalView(QWidget):
    """Zeigt eine Sitzung. Der Prozess startet kurz nach ``start`` mit der dann bekannten Größe."""

    closed = Signal(int)  # Prozess beendet (Exit-Code)
    copied = Signal(str)  # Text wurde ausdrücklich kopiert (Strg+Umschalt+C, Menü)
    history_requested = Signal()  # Strg+Umschalt+H oder Menü: Zwischenablage-Verlauf öffnen
    split_requested = Signal(int)  # Strg+Umschalt+O (rechts) / E (unten) oder Menü: Wert von ``Qt.Orientation``
    focus_requested = Signal(str)  # Alt+Pfeil: ``left``/``right``/``up``/``down``
    zoom_requested = Signal()  # Strg+Umschalt+Enter oder Menü: Kachel vergrößern bzw. zurück
    close_requested = Signal()  # Strg+Umschalt+W oder Menü: diese Kachel schließen
    focused = Signal()  # die Kachel hat den Fokus bekommen

    def __init__(self, title: str, scale: int = 100, dark: bool = True, parent=None):
        super().__init__(parent)
        self.title = title
        self._scheme = SCHEMES["dark" if dark else "light"]
        self._scale = scale
        self._zoom = 0
        self._cols, self._rows = 80, 24
        self._offset = 0  # Zeilen, um die zurückgescrollt wurde
        self._sel: tuple[tuple[int, int], tuple[int, int]] | None = None  # ((Zeile, Spalte), …) in absoluten Zeilen
        self._dragging = False
        self._pending: tuple[list[str], dict[str, str]] | None = None
        self._command: tuple[list[str], dict[str, str]] | None = None  # Befehl der Sitzung (für „Kachel teilen“)
        self._tiled = False  # mehrere Kacheln im Reiter: aktive Kachel bekommt einen Rand
        self._pty: pty_session.PtySession | None = None
        self._finished: int | None = None
        self._notice = ""
        self._cell_w, self._cell_h, self._ascent = 8.0, 16.0, 12.0
        self._fonts: dict[bool, QFont] = {}
        self._apply_font()

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled, True)
        self.setCursor(Qt.CursorShape.IBeamCursor)
        self.setMouseTracking(False)
        self.setMinimumSize(200, 100)
        self._screen = _Screen(self._cols, self._rows, self._reply)
        self._stream = pyte.ByteStream(self._screen)
        self._repaint = QTimer(self)
        self._repaint.setSingleShot(True)
        self._repaint.setInterval(10)  # Ausgabe bündeln: bei großen Mengen nicht jedes Stück einzeln zeichnen
        self._repaint.timeout.connect(self.update)

    # --- Start / Ende ---
    def start(self, args: list[str], env: dict[str, str]) -> None:
        self._pending = (args, env)
        self._command = (list(args), dict(env))
        QTimer.singleShot(0, self._maybe_spawn)

    def _maybe_spawn(self):
        if self._pending is None:
            return
        # Auch Reiter im Hintergrund starten (z. B. ein Snippet auf vielen Hosts); ihre Größe passt resizeEvent an, sobald sie sichtbar werden
        if self.width() >= 100 and self.height() >= 60:
            self._fit()
        args, env = self._pending
        self._pending = None
        env = dict(env)
        env["TERM"] = "xterm-256color"
        env["COLORTERM"] = "truecolor"
        session = pty_session.create(self)
        session.output.connect(self._on_output)
        session.finished.connect(self._on_finished)
        try:
            session.start(args, env, self._cols, self._rows)
        except pty_session.PtyError as exc:
            self._finished = -1
            self._notice = tr("Das Terminal konnte nicht gestartet werden: {exc}", exc=exc)
            self._feed(("\x1b[31m" + self._notice + "\x1b[0m\r\n").encode())
            return
        self._pty = session

    @property
    def command(self) -> tuple[list[str], dict[str, str]] | None:
        """Argumente und Umgebung, mit denen die Sitzung gestartet wurde (``None`` vor dem Start)."""
        return self._command

    def set_tiled(self, tiled: bool) -> None:
        if tiled != self._tiled:
            self._tiled = tiled
            self.update()

    def poll(self) -> int | None:
        """Für die Verbindungsverwaltung: ``None``, solange die Sitzung läuft."""
        return self._finished

    def failed_to_start(self) -> bool:
        return bool(self._notice)

    def terminate(self, force: bool = False) -> None:
        self._pending = None
        if self._pty is not None:
            self._pty.terminate(force)
        elif self._finished is None:
            self._finished = 0
            self.closed.emit(0)

    def _on_finished(self, code: int):
        self._finished = code
        self.closed.emit(code)

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self._maybe_spawn)

    # --- Schrift, Farben, Größe ---
    def _apply_font(self):
        base = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        base.setStyleHint(QFont.StyleHint.Monospace)
        size = max(6.0, min(40.0, 10.0 * self._scale / 100 + self._zoom))
        base.setPointSizeF(size)
        base.setKerning(False)
        bold = QFont(base)
        bold.setBold(True)
        self._fonts = {False: base, True: bold}
        fm = QFontMetricsF(base)
        self._cell_w = max(1.0, fm.horizontalAdvance("M"))
        self._cell_h = max(1.0, float(int(fm.height() + 0.999)))
        self._ascent = fm.ascent()

    def set_scale(self, percent: int):
        self._scale = percent
        self._apply_font()
        self._fit()
        self.update()

    def set_dark(self, dark: bool):
        self._scheme = SCHEMES["dark" if dark else "light"]
        self.update()

    def _fit(self):
        cols = max(2, int((self.width() - 2 * PADDING) / self._cell_w))
        rows = max(1, int((self.height() - 2 * PADDING) / self._cell_h))
        if (cols, rows) == (self._cols, self._rows):
            return
        self._cols, self._rows = cols, rows
        self._screen.resize(rows, cols)
        if self._pty is not None:
            self._pty.resize(cols, rows)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit()
        if self._pending is not None:
            QTimer.singleShot(0, self._maybe_spawn)
        self._offset = min(self._offset, len(self._screen.scrollback))

    # --- Ausgabe ---
    def _reply(self, data: str):
        if self._pty is not None:
            self._pty.write(data.encode("utf-8"))

    def _on_output(self, data: bytes):
        self._feed(data)

    def _feed(self, data: bytes):
        sb = self._screen.scrollback
        before_len, before_scrolled = len(sb), self._screen.scrolled
        self._stream.feed(data)
        added = self._screen.scrolled - before_scrolled
        dropped = max(0, before_len + added - len(sb))
        if dropped and self._sel:
            (r1, c1), (r2, c2) = self._sel
            self._sel = ((r1 - dropped, c1), (r2 - dropped, c2))
        if self._offset:  # beim Zurückblättern bleibt die Ansicht stehen
            self._offset = min(len(sb), max(0, self._offset + added - dropped))
        if not self._repaint.isActive():
            self._repaint.start()

    # --- Zeilen und Zellen ---
    def _first_row(self) -> int:
        """Absolute Nummer der obersten sichtbaren Zeile (Scrollback + Bildschirm)."""
        if self._screen.in_alt:
            return len(self._screen.scrollback)
        return len(self._screen.scrollback) - self._offset

    def _row_cells(self, absolute: int) -> list:
        sb = self._screen.scrollback
        if absolute < len(sb):
            row = sb[absolute]
            blank = self._screen.default_char
            return row + [blank] * (self._cols - len(row)) if len(row) < self._cols else row
        y = absolute - len(sb)
        if y >= self._screen.lines:
            return [self._screen.default_char] * self._cols
        buf = self._screen.buffer[y]
        return [buf[x] for x in range(self._screen.columns)]

    def _color(self, name: str, default: str) -> QColor:
        if name == "default":
            return QColor(default)
        ansi = self._scheme["ansi"]
        if name in _ANSI:
            return QColor(ansi[_ANSI.index(name)])
        if name.startswith("bright") and name[6:] in _ANSI:
            return QColor(ansi[8 + _ANSI.index(name[6:])])
        if len(name) == 6:
            color = QColor("#" + name)
            if color.isValid():
                return color
        return QColor(default)

    def _selected(self, absolute: int, col: int) -> bool:
        if not self._sel:
            return False
        (r1, c1), (r2, c2) = sorted(self._sel)
        return (r1, c1) <= (absolute, col) <= (r2, c2)

    # --- Zeichnen ---
    def paintEvent(self, event):
        p = QPainter(self)
        scheme = self._scheme
        bg_default, fg_default = scheme["bg"], scheme["fg"]
        p.fillRect(self.rect(), QColor(bg_default))
        cw, ch = self._cell_w, self._cell_h
        first = self._first_row()
        screen = self._screen
        selection_color = QColor(scheme["selection"])
        for row in range(self._rows):
            absolute = first + row
            cells = self._row_cells(absolute)
            y = PADDING + row * ch
            runs: list[tuple[int, int, tuple]] = []  # (Start, Ende, Stil)
            for x in range(min(self._cols, len(cells))):
                c = cells[x]
                fg, bg = c.fg, c.bg
                if c.reverse:
                    fg, bg = bg, fg
                    fg = fg if fg != "default" else "__bg"
                    bg = bg if bg != "default" else "__fg"
                style = (fg, bg, c.bold, c.italics, c.underscore, c.strikethrough, self._selected(absolute, x))
                if runs and runs[-1][2] == style and runs[-1][1] == x:
                    runs[-1] = (runs[-1][0], x + 1, style)
                else:
                    runs.append((x, x + 1, style))
            for start, end, (fg, bg, bold, italics, under, strike, selected) in runs:
                x0 = PADDING + start * cw
                rect = QRectF(x0, y, (end - start) * cw, ch)
                if selected:
                    p.fillRect(rect, selection_color)
                elif bg != "default":
                    p.fillRect(rect, QColor(fg_default) if bg == "__fg" else self._color(bg, bg_default))
                text = "".join(cells[x].data or "" for x in range(start, end))
                if not text.strip() and not under and not strike:
                    continue
                color = QColor(bg_default) if fg == "__bg" else self._color(fg, fg_default)
                p.setPen(color)
                font = self._fonts[bold]
                if italics:
                    font = QFont(font)
                    font.setItalic(True)
                p.setFont(font)
                self._draw_text(p, cells, start, end, x0, y)
                if under:
                    p.setPen(QPen(color, 1))
                    p.drawLine(QPointF(x0, y + ch - 2), QPointF(x0 + (end - start) * cw, y + ch - 2))
                if strike:
                    p.setPen(QPen(color, 1))
                    p.drawLine(QPointF(x0, y + ch / 2), QPointF(x0 + (end - start) * cw, y + ch / 2))
        self._paint_cursor(p, first, cells_cache=None)
        if self._tiled:  # Rand um die aktive Kachel, die übrigen nur dezent umrandet
            p.setPen(QPen(QColor(scheme["cursor"] if self.hasFocus() else scheme["selection"]), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(QRectF(1, 1, self.width() - 2, self.height() - 2))

    def _draw_text(self, p: QPainter, cells, start: int, end: int, x0: float, y: float):
        """ASCII-Folgen am Stück, alles andere (Umlaute, Rahmenzeichen, breite Zeichen) einzeln auf das Zellraster."""
        cw, base = self._cell_w, y + self._ascent
        x = start
        while x < end:
            data = cells[x].data
            if data and " " <= data <= "~":
                stop = x
                while stop < end and cells[stop].data and " " <= cells[stop].data <= "~":
                    stop += 1
                p.drawText(QPointF(x0 + (x - start) * cw, base), "".join(cells[i].data for i in range(x, stop)))
                x = stop
            else:
                if data and data != " ":
                    p.drawText(QPointF(x0 + (x - start) * cw, base), data)
                x += 1

    def _paint_cursor(self, p: QPainter, first: int, cells_cache):
        screen = self._screen
        if screen.cursor.hidden or self._finished is not None:
            return
        absolute = len(screen.scrollback) + screen.cursor.y
        row = absolute - first
        if not (0 <= row < self._rows) or screen.cursor.x >= self._cols:
            return
        rect = QRectF(PADDING + screen.cursor.x * self._cell_w, PADDING + row * self._cell_h, self._cell_w, self._cell_h)
        cursor_color = QColor(self._scheme["cursor"])
        if self.hasFocus():
            p.fillRect(rect, cursor_color)
            cell = self._row_cells(absolute)[screen.cursor.x]
            if cell.data and cell.data != " ":
                p.setPen(QColor(self._scheme["bg"]))
                p.setFont(self._fonts[bool(cell.bold)])
                p.drawText(QPointF(rect.x(), rect.y() + self._ascent), cell.data)
        else:
            p.setPen(QPen(cursor_color, 1))
            p.drawRect(rect.adjusted(0.5, 0.5, -0.5, -0.5))

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.update()
        self.focused.emit()

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.update()

    # --- Tastatur ---
    FOCUS_KEYS = {Qt.Key.Key_Left: "left", Qt.Key.Key_Right: "right", Qt.Key.Key_Up: "up", Qt.Key.Key_Down: "down"}

    def focusNextPrevChild(self, next_):  # Tab gehört dem Terminal
        return False

    def event(self, ev):
        if ev.type() == ev.Type.ShortcutOverride:  # Kürzel des Hauptfensters (Strg+K, Entf, F2 …) sollen im Terminal ankommen
            ev.accept()
            return True
        return super().event(ev)

    def _send(self, data: bytes):
        if self._pty is not None and self._finished is None:
            self._pty.write(data)

    def keyPressEvent(self, ev: QKeyEvent):
        key, mods = ev.key(), ev.modifiers()
        ctrl_shift = Mod.ControlModifier | Mod.ShiftModifier
        if (mods & ctrl_shift) == ctrl_shift and key == Qt.Key.Key_C or (mods == Mod.ControlModifier and key == Qt.Key.Key_Insert):
            self.copy()
            return
        if (mods & ctrl_shift) == ctrl_shift and key == Qt.Key.Key_H:
            self.history_requested.emit()
            return
        if (mods & ctrl_shift) == ctrl_shift and key == Qt.Key.Key_O:
            self.split_requested.emit(Qt.Orientation.Horizontal.value)
            return
        if (mods & ctrl_shift) == ctrl_shift and key == Qt.Key.Key_E:
            self.split_requested.emit(Qt.Orientation.Vertical.value)
            return
        if (mods & ctrl_shift) == ctrl_shift and key == Qt.Key.Key_W:
            self.close_requested.emit()
            return
        if (mods & ctrl_shift) == ctrl_shift and key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.zoom_requested.emit()
            return
        if mods == Mod.AltModifier and key in self.FOCUS_KEYS:
            self.focus_requested.emit(self.FOCUS_KEYS[key])
            return
        if (mods & ctrl_shift) == ctrl_shift and key == Qt.Key.Key_V or (mods == Mod.ShiftModifier and key == Qt.Key.Key_Insert):
            self.paste()
            return
        if mods == Mod.ShiftModifier and key in (Qt.Key.Key_PageUp, Qt.Key.Key_PageDown):
            self._scroll(self._rows - 1 if key == Qt.Key.Key_PageUp else -(self._rows - 1))
            return
        if key in (Qt.Key.Key_Shift, Qt.Key.Key_Control, Qt.Key.Key_Alt, Qt.Key.Key_Meta, Qt.Key.Key_AltGr, Qt.Key.Key_CapsLock):
            return
        data = key_bytes(key, ev.text(), mods, APP_CURSOR_KEYS in self._screen.mode)
        if data is None:
            super().keyPressEvent(ev)
            return
        self._scroll_to_end()
        self._send(data)

    def inputMethodEvent(self, ev):  # Tote Tasten, Compose, Eingabemethoden
        text = ev.commitString()
        if text:
            self._scroll_to_end()
            self._send(text.encode("utf-8"))
        ev.accept()

    # --- Scrollen ---
    def _scroll(self, lines: int):
        if self._screen.in_alt:
            return
        new = max(0, min(len(self._screen.scrollback), self._offset + lines))
        if new != self._offset:
            self._offset = new
            self.update()

    def _scroll_to_end(self):
        if self._offset:
            self._offset = 0
            self.update()

    def wheelEvent(self, ev):
        delta = ev.angleDelta().y()
        if not delta:
            return
        if ev.modifiers() & Mod.ControlModifier:  # Strg+Rad: Schriftgröße nur dieses Terminals
            self._zoom = max(-4, min(20, self._zoom + (1 if delta > 0 else -1)))
            self._apply_font()
            self._fit()
            self.update()
            return
        steps = max(1, abs(delta) // 40)
        if self._screen.in_alt:  # vim, less, htop: Rad wie Pfeiltasten
            key = Qt.Key.Key_Up if delta > 0 else Qt.Key.Key_Down
            self._send(key_bytes(key, "", Mod.NoModifier, APP_CURSOR_KEYS in self._screen.mode) * steps)
        else:
            self._scroll(steps if delta > 0 else -steps)

    # --- Maus, Auswahl ---
    def _cell_at(self, pos) -> tuple[int, int]:
        col = max(0, min(self._cols - 1, int((pos.x() - PADDING) / self._cell_w)))
        row = max(0, min(self._rows - 1, int((pos.y() - PADDING) / self._cell_h)))
        return self._first_row() + row, col

    def mousePressEvent(self, ev):
        self.setFocus()
        if ev.button() == Qt.MouseButton.LeftButton:
            cell = self._cell_at(ev.position())
            self._sel = (cell, cell)
            self._dragging = True
            self.update()
        elif ev.button() == Qt.MouseButton.MiddleButton:
            text = QGuiApplication.clipboard().text(QClipboard.Mode.Selection)
            if text:
                self._paste_text(text)

    def mouseMoveEvent(self, ev):
        if self._dragging and self._sel:
            pos = ev.position()
            if pos.y() < 0:
                self._scroll(1)
            elif pos.y() > self.height():
                self._scroll(-1)
            self._sel = (self._sel[0], self._cell_at(pos))
            self.update()

    def mouseReleaseEvent(self, ev):
        if ev.button() != Qt.MouseButton.LeftButton or not self._dragging:
            return
        self._dragging = False
        if self._sel and self._sel[0] == self._sel[1]:
            self._sel = None
        elif self._sel:
            text = self.selected_text()
            if text:
                QGuiApplication.clipboard().setText(text, QClipboard.Mode.Selection)
        self.update()

    def mouseDoubleClickEvent(self, ev):
        if ev.button() != Qt.MouseButton.LeftButton:
            return
        absolute, col = self._cell_at(ev.position())
        cells = self._row_cells(absolute)

        def word_char(i):
            d = cells[i].data
            return bool(d) and not d.isspace() and d not in "\"'()[]{}<>,;|"

        if not word_char(col):
            return
        start = end = col
        while start > 0 and word_char(start - 1):
            start -= 1
        while end < len(cells) - 1 and word_char(end + 1):
            end += 1
        self._sel = ((absolute, start), (absolute, end))
        self._dragging = False
        text = self.selected_text()
        if text:
            QGuiApplication.clipboard().setText(text, QClipboard.Mode.Selection)
        self.update()

    def selected_text(self) -> str:
        if not self._sel:
            return ""
        (r1, c1), (r2, c2) = sorted(self._sel)
        lines = []
        for absolute in range(r1, r2 + 1):
            cells = self._row_cells(absolute)
            lo = c1 if absolute == r1 else 0
            hi = c2 if absolute == r2 else len(cells) - 1
            text = "".join((cells[x].data or "") for x in range(lo, min(hi, len(cells) - 1) + 1))
            lines.append(text.rstrip() if absolute != r2 or hi >= len(cells) - 1 else text)
        return "\n".join(lines)

    def select_all(self):
        last = len(self._screen.scrollback) + self._screen.lines - 1
        self._sel = ((0, 0), (last, max(0, self._cols - 1)))
        self.update()

    def copy(self):
        text = self.selected_text()
        if text:
            QGuiApplication.clipboard().setText(text)
            self.copied.emit(text)

    def paste(self):
        self._paste_text(QGuiApplication.clipboard().text())

    def paste_text(self, text: str, enter: bool = False):
        """Fügt Text ein (bei mehrzeiligem Text im eingeklammerten Einfügemodus); ``enter`` sendet danach Enter."""
        self._paste_text(text)
        if text and enter:
            self._send(b"\r")

    def _paste_text(self, text: str):
        if not text:
            return
        text = text.replace("\r\n", "\r").replace("\n", "\r")
        if BRACKETED_PASTE in self._screen.mode:
            text = "\x1b[200~" + text.replace("\x1b", "") + "\x1b[201~"
        self._scroll_to_end()
        self._send(text.encode("utf-8"))

    def clear_scrollback(self):
        self._screen.scrollback.clear()
        self._offset = 0
        self._sel = None
        self.update()

    def contextMenuEvent(self, ev):
        menu = QMenu(self)
        copy = menu.addAction(tr("Kopieren"))
        copy.setEnabled(bool(self._sel and self.selected_text()))
        copy.triggered.connect(self.copy)
        menu.addAction(tr("Einfügen")).triggered.connect(self.paste)
        menu.addAction(tr("Alles auswählen")).triggered.connect(self.select_all)
        menu.addAction(tr("Verlauf …")).triggered.connect(self.history_requested.emit)
        menu.addSeparator()
        menu.addAction(tr("Verlauf löschen")).triggered.connect(self.clear_scrollback)
        menu.addSeparator()
        menu.addAction(tr("Rechts teilen")).triggered.connect(lambda: self.split_requested.emit(Qt.Orientation.Horizontal.value))
        menu.addAction(tr("Unten teilen")).triggered.connect(lambda: self.split_requested.emit(Qt.Orientation.Vertical.value))
        menu.addAction(tr("Kachel vergrößern / zurück")).triggered.connect(self.zoom_requested.emit)
        menu.addAction(tr("Kachel schließen")).triggered.connect(self.close_requested.emit)
        menu.exec(ev.globalPos())
