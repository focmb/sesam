"""Schnellstarter (Strg+K): Verbindung tippen, Enter verbindet. Favoriten und zuletzt Verwendete stehen oben."""
from __future__ import annotations

import html
import time

from PySide6.QtCore import QEvent, QSize, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout, QWidget

from .. import colors, quick
from ..i18n import tr
from ..models import Config
from . import safe, style, theme

HIT_ROLE = int(Qt.ItemDataRole.UserRole)
GOLD = "#e8a317"
SECTION_TITLES = {quick.FAVORITES: "Favoriten", quick.RECENT: "Zuletzt verwendet", quick.ALL: "Alle Verbindungen"}


def highlight(text: str, positions: list[int], color: str) -> str:
    """Text als HTML, die gesuchten Zeichen fett und farbig. Alles andere ist maskiert (Namen kommen auch aus dem Sync-Repository)."""
    marked = set(positions)
    out, i = [], 0
    while i < len(text):
        if i in marked:
            j = i
            while j < len(text) and j in marked:
                j += 1
            out.append(f'<b style="color:{color}">{html.escape(text[i:j])}</b>')
            i = j
        else:
            j = i
            while j < len(text) and j not in marked:
                j += 1
            out.append(html.escape(text[i:j]))
            i = j
    return "".join(out)


class QuickLauncher(QDialog):
    """Schlankes Suchfenster über dem Hauptfenster. Ergebnis: ``action`` ("connect" oder "files") und ``conn_id``."""

    def __init__(self, cfg: Config, toggle_favorite, parent=None, now=time.time):
        super().__init__(parent)
        self.cfg = cfg
        self._toggle_favorite = toggle_favorite
        self._now = now
        self.action = ""
        self.conn_id = ""
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("quickLauncher")
        self.setFixedWidth(680)

        self.input = QLineEdit()
        self.input.setPlaceholderText(tr("Verbindung suchen (Name, Host, Benutzer, Gruppe, Tag) …"))
        self.input.textChanged.connect(self._fill)
        self.input.installEventFilter(self)
        self.list = QListWidget()
        self.list.setFrameShape(QListWidget.Shape.NoFrame)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.list.itemActivated.connect(lambda *_: self._choose("connect"))
        self.list.itemClicked.connect(lambda *_: self._choose("connect"))
        self.hint = QLabel(tr("↵ Verbinden   ·   Strg+↵ Dateien (SFTP)   ·   Strg+B Favorit   ·   Esc Schließen"))
        self.hint.setObjectName("hint")
        self.hint.setContentsMargins(14, 8, 14, 10)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 0)
        layout.setSpacing(8)
        layout.addWidget(self.input)
        layout.addWidget(self.list, 1)
        layout.addWidget(self.hint)
        self._fill()

    # --- Inhalt ---
    def _fill(self, *_, keep: str = ""):
        c = theme.colors()
        hits = quick.search(self.cfg, self.input.text())
        self.list.clear()
        now = self._now()
        section = None
        select: QListWidgetItem | None = None
        first: QListWidgetItem | None = None
        for hit in hits:
            if hit.section != section:
                section = hit.section
                head = QListWidgetItem(tr(SECTION_TITLES[section]).upper())
                head.setFlags(Qt.ItemFlag.NoItemFlags)
                font = head.font()
                font.setPointSizeF(max(7.0, font.pointSizeF() * 0.82))
                head.setFont(font)
                self.list.addItem(head)
            item = QListWidgetItem()
            item.setData(HIT_ROLE, hit.conn.id)
            item.setSizeHint(self._row_height())
            self.list.addItem(item)
            self.list.setItemWidget(item, self._row(hit, now, c))
            first = first or item
            if keep and hit.conn.id == keep:
                select = item
        target = select or first
        if target is not None:
            self.list.setCurrentItem(target)
        shown = min(self.list.count(), 12)
        self.list.setFixedHeight(max(60, shown * self._row_height().height() + 6))
        self.adjustSize()

    def _row_height(self):
        return QSize(0, max(34, int(self.fontMetrics().height() * 2.1)))

    def _row(self, hit, now: float, c: dict[str, str]) -> QWidget:
        conn = hit.conn
        row = QWidget()
        row.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        row.setStyleSheet("background: transparent;")
        lay = QHBoxLayout(row)
        lay.setContentsMargins(10, 0, 10, 0)
        lay.setSpacing(8)
        icon = QLabel()
        icon.setPixmap(style.icon("server", colors.hex_of(conn.color) or c["muted"]).pixmap(18, 18))
        name = QLabel()
        name.setTextFormat(Qt.TextFormat.RichText)  # nur selbst gebautes, maskiertes HTML (siehe highlight)
        name.setText(highlight(conn.name, hit.positions, c["accent"]))
        lay.addWidget(icon)
        lay.addWidget(name)
        if conn.favorite and hit.section != quick.FAVORITES:
            star = QLabel()
            star.setPixmap(style.icon("star", GOLD, fill=GOLD, size=14).pixmap(14, 14))
            lay.addWidget(star)
        target = f"{conn.user}@{conn.host}" if conn.user else conn.host
        detail = safe.plain(QLabel(" / ".join(p for p in (conn.group, target) if p)))
        detail.setStyleSheet(f"color: {c['muted']};")
        lay.addWidget(detail, 1)
        if hit.used:
            when = safe.plain(QLabel(quick.ago(now - hit.used)))
            when.setStyleSheet(f"color: {c['muted']};")
            lay.addWidget(when)
        return row

    # --- Bedienung ---
    def _selectable(self) -> list[int]:
        return [i for i in range(self.list.count()) if self.list.item(i).flags() & Qt.ItemFlag.ItemIsSelectable]

    def _move(self, step: int):
        rows = self._selectable()
        if not rows:
            return
        current = self.list.currentRow()
        pos = rows.index(current) if current in rows else 0
        self.list.setCurrentRow(rows[max(0, min(len(rows) - 1, pos + step))])

    def current_id(self) -> str:
        item = self.list.currentItem()
        return item.data(HIT_ROLE) if item is not None and item.data(HIT_ROLE) else ""

    def _choose(self, action: str):
        cid = self.current_id()
        if cid:
            self.action, self.conn_id = action, cid
            self.accept()

    def _favorite(self):
        cid = self.current_id()
        if cid:
            self._toggle_favorite(cid)
            self._fill(keep=cid)

    def eventFilter(self, obj, event):
        if obj is self.input and event.type() == QEvent.Type.KeyPress:
            key, mods = event.key(), event.modifiers()
            ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
            if key in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                self._move(1 if key == Qt.Key.Key_Down else -1)
                return True
            if key in (Qt.Key.Key_PageDown, Qt.Key.Key_PageUp):
                self._move(8 if key == Qt.Key.Key_PageDown else -8)
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._choose("files" if ctrl else "connect")
                return True
            if ctrl and key == Qt.Key.Key_B:
                self._favorite()
                return True
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            return  # Enter gehört dem Suchfeld, nie einer Standardschaltfläche
        super().keyPressEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        parent = self.parentWidget()
        if parent is not None:
            geo = parent.frameGeometry()
            self.move(geo.center().x() - self.width() // 2, geo.top() + 90)
        self.input.setFocus()
