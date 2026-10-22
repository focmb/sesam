from __future__ import annotations

import copy
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QAction, QActionGroup, QBrush, QColor, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QButtonGroup, QFrame, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QMainWindow, QMenu,
    QMessageBox, QPushButton,
    QTreeWidget, QTreeWidgetItem, QTreeWidgetItemIterator, QVBoxLayout, QWidget,
)

from .. import __version__
from .. import i18n
from ..i18n import tr
from .. import groups as grp
from . import style, theme
from .. import colors, docker, launcher, passwords, sftp, snippets, storage
from .. import tags as tag_util
from ..models import Config, Connection, Terminal, new_id
from ..ssh_config import parse_ssh_config
from .connection_dialog import ConnectionDialog, swatch
from .docker_dialog import DockerConnectDialog
from .identities_dialog import IdentitiesDialog
from .keys_dialog import KeysDialog
from .settings_dialog import SettingsDialog
from .snippets_dialog import SnippetsDialog
from .jobs import Job
from .sftp_window import SftpWindow
from .sync_controller import SyncController
from .sync_dialog import SyncDialog
from .terminals_dialog import TerminalsDialog

ID_ROLE = int(Qt.ItemDataRole.UserRole)  # Verbindungs-ID
GROUP_ROLE = ID_ROLE + 1  # Gruppenpfad
CONTAINER_ROLE = ID_ROLE + 2  # „<ID der Host-Verbindung>\x1f<Containername>“


def _submenu(menu: QMenu, title: str) -> QMenu:
    """Untermenü, das dem Elternmenü gehört (``addMenu(Text)`` lässt Python die Besitzfrage offen und das Menü kann verschwinden)."""
    sub = QMenu(title, menu)
    menu.addMenu(sub)
    return sub


class ConnectionTree(QTreeWidget):
    """Baum mit Drag & Drop; die eigentliche Verschiebung übernimmt das Hauptfenster."""

    moved = Signal(str, str, str)  # Art ("conn"/"group"), Schlüssel (ID bzw. Pfad), Zielgruppe

    def __init__(self):
        super().__init__()
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)  # Strg/Umschalt für Mehrfachauswahl

    def dropEvent(self, event):
        source = self.currentItem()
        target = self.itemAt(event.position().toPoint())
        # Qt soll nichts selbst verschieben; wir ändern die Daten und bauen den Baum neu auf.
        event.setDropAction(Qt.DropAction.IgnoreAction)
        event.accept()
        if source is None:
            return
        if target is None:
            target_group = ""
        elif target.data(0, GROUP_ROLE) is not None:
            target_group = target.data(0, GROUP_ROLE)
        else:
            parent = target.parent()
            while parent is not None and parent.data(0, GROUP_ROLE) is None:  # Container/Verbindung → zur Gruppe darüber
                parent = parent.parent()
            target_group = parent.data(0, GROUP_ROLE) if parent else ""
        if source.data(0, GROUP_ROLE) is not None:
            self.moved.emit("group", source.data(0, GROUP_ROLE), target_group)
        elif source.data(0, ID_ROLE):
            ids = [i.data(0, ID_ROLE) for i in self.selectedItems() if i.data(0, ID_ROLE) and i.data(0, GROUP_ROLE) is None]
            if source.data(0, ID_ROLE) not in ids:
                ids = [source.data(0, ID_ROLE)]
            for cid in ids:  # alle ausgewählten Verbindungen zusammen verschieben
                self.moved.emit("conn", cid, target_group)


class MainWindow(QMainWindow):
    def __init__(self, cfg: Config, path: Path):
        super().__init__()
        self.cfg = cfg
        self.path = path
        self._sftp_windows: list[SftpWindow] = []  # offene Dateibrowser (SFTP)
        # Docker-Container je Host (nur zur Laufzeit): ID → Liste, Fehlertext oder None (= wird geladen)
        self._containers: dict[str, list[docker.Container] | str | None] = {}
        self._docker_gen: dict[str, int] = {}  # Zähler, damit veraltete Antworten verworfen werden
        self._docker_expanded: set[str] = set()  # aufgeklappte Hosts
        self._docker_passwords: dict[str, str] = {}  # nachgefragte Passwörter, nur im Speicher
        self._docker_jobs: set[Job] = set()
        self._lost_key: tuple | None = None
        self._rebuilding = 0  # >0, solange der Baum programmatisch umgebaut wird
        # zugeklappte Gruppen: aus der Konfiguration wiederherstellen, Verwaistes (gelöschte Gruppen) verwerfen
        self.collapsed: set[str] = set(cfg.collapsed_groups) & set(grp.all_groups(cfg))
        self.setWindowTitle("Sesam")
        self._restore_window_size()

        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("Suchen (Name, Host, Benutzer, Gruppe) …"))
        self.search.textChanged.connect(self.refresh)
        self.search.textChanged.connect(self._search_text_changed)

        self.tree = ConnectionTree()
        self.tree.setHeaderLabels([tr("Name"), tr("Ziel"), tr("Terminal"), tr("Tags")])
        self.tree.setAlternatingRowColors(False)
        self.tree.setIndentation(22)
        self.tree.setUniformRowHeights(True)
        self.tree.setExpandsOnDoubleClick(False)  # Doppelklick verbindet; Gruppen klappen wir selbst auf
        self.tree.setIconSize(QSize(18, 18))
        header = self.tree.header()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)  # Breite frei ziehbar
        header.setStretchLastSection(True)
        header.setMinimumSectionSize(60)
        self._apply_column_widths()
        self._collapse_timer = QTimer(self)  # Einklapp-Zustand kurz nach dem Umschalten speichern
        self._collapse_timer.setSingleShot(True)
        self._collapse_timer.setInterval(600)
        self._collapse_timer.timeout.connect(self._flush_collapsed)
        self._width_timer = QTimer(self)
        self._width_timer.setSingleShot(True)
        self._width_timer.setInterval(600)
        self._width_timer.timeout.connect(self._flush_widths)
        header.sectionResized.connect(lambda *_: self._width_timer.start())
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        self.tree.itemDoubleClicked.connect(self._double_clicked)
        self.tree.itemCollapsed.connect(lambda it: self._expansion_changed(it, True))
        self.tree.itemExpanded.connect(lambda it: self._expansion_changed(it, False))
        self.tree.moved.connect(lambda k, key, tgt: QTimer.singleShot(0, lambda: self._move(k, key, tgt)))

        central = QWidget()
        outer = QHBoxLayout(central)
        outer.setContentsMargins(14, 10, 14, 10)
        outer.setSpacing(14)
        layout = QVBoxLayout()
        layout.setSpacing(10)
        layout.addWidget(self.search)
        layout.addWidget(self.tree)
        outer.addLayout(layout, 1)
        self._outer_layout = outer  # die Seitenleiste kommt rechts dazu, sobald die Aktionen existieren
        self.setCentralWidget(central)

        self.sync = SyncController(self.cfg, lambda: storage.save(self.cfg, self.path), self)
        self.sync.status.connect(self._sync_status)
        self.sync.cfg_changed.connect(self._sync_changed_config)
        self.sync.finished.connect(self._sync_finished)
        self.sync_label = QLabel("")
        self.statusBar().addPermanentWidget(self.sync_label)
        self.version_label = QLabel(tr("Version {__version__}", __version__=__version__))  # ganz links in der Statuszeile
        self.version_label.setObjectName("versionLabel")
        self.version_label.setToolTip("Sesam")
        self.version_label.setContentsMargins(12, 0, 0, 0)  # bündig mit der Verbindungsliste
        self.statusBar().addWidget(self.version_label)
        self.statusBar().setSizeGripEnabled(False)

        self._build_actions()
        self.refresh()
        if self.sync.enabled() and self.cfg.sync.auto:
            QTimer.singleShot(500, lambda: self.sync.sync())  # beim Start abgleichen

    # --- Aufbau ---
    def _build_actions(self):
        def act(text, slot, shortcut=None):
            a = QAction(text, self)
            a.triggered.connect(lambda *_: slot())
            if isinstance(shortcut, (list, tuple)):
                a.setShortcuts([QKeySequence(k) for k in shortcut])
            elif shortcut:
                a.setShortcut(QKeySequence(shortcut))
            return a

        self.a_connect = act(tr("Verbinden"), self.connect_selected, "Return")
        self.a_files = act(tr("Dateien (SFTP)"), self.open_files, "F4")
        self.a_new = act(tr("Neue Verbindung"), self.new_connection, "Ctrl+N")
        self.a_new_group = act(tr("Neue Gruppe …"), self.new_group, "Ctrl+Shift+N")
        self.a_edit = act(tr("Bearbeiten"), self.edit_selected, "F2")
        self.a_dup = act(tr("Duplizieren"), self.duplicate_selected, "Ctrl+D")
        self.a_del = act(tr("Löschen"), self.delete_selected, "Del")
        self.a_terms = act(tr("Terminals …"), self.edit_terminals)
        self.a_identities = act(tr("Identitäten …"), self.edit_identities)
        self.a_keys = act(tr("Schlüssel …"), self.edit_keys)
        self.a_snippets = act(tr("Snippets …"), self.edit_snippets, "Ctrl+Shift+S")
        self.a_sync = act(tr("Jetzt synchronisieren"), lambda: self.sync.sync(manual=True), "F5")
        self.a_sync_settings = act(tr("Synchronisation …"), self.edit_sync)
        self.a_import = act(tr("Aus ~/.ssh/config importieren"), self.import_ssh_config)

        self.a_quit = act(tr("Beenden"), self.close, "Ctrl+Q")
        self.addAction(self.a_quit)  # kein Menü mehr: das Kürzel hängt direkt am Fenster

        # Schriftgröße: Die Einstellung selbst liegt im Einstellungsdialog; die Tastenkürzel hängen direkt am Fenster
        self.a_font_bigger = act(tr("Größer"), lambda: self.set_font_scale(theme.next_scale(self.cfg.font_scale, +1)), ["Ctrl++", "Ctrl+="])
        self.a_font_smaller = act(tr("Kleiner"), lambda: self.set_font_scale(theme.next_scale(self.cfg.font_scale, -1)), "Ctrl+-")
        self.a_font_reset = act(tr("Zurücksetzen (100 %)"), lambda: self.set_font_scale(100), "Ctrl+0")
        self.a_settings = act(tr("Einstellungen"), self.edit_settings)
        for a in (self.a_font_bigger, self.a_font_smaller, self.a_font_reset):
            self.addAction(a)

        self._build_sidebar()
        theme.notifier().changed.connect(self._theme_changed)
        self._refresh_icons()

    # --- Seitenleiste ---
    THEME_TIPS = {"light": "Hell", "dark": "Dunkel", "system": "Wie das System"}
    THEME_ICONS = {"light": "sun", "dark": "moon", "system": "monitor"}
    SIDEBAR_MIN_WIDTH = 190
    SIDEBAR_GROUPS = (
        ("a_connect", "a_files", "a_new", "a_new_group", "a_edit", "a_dup", "a_del"),
        ("a_sync", "a_sync_settings", "a_identities", "a_keys", "a_snippets", "a_terms"),
    )

    def _build_sidebar(self):
        """Alle Hauptaktionen untereinander in einer Leiste am rechten Rand."""
        side = QWidget()
        side.setObjectName("sidebar")
        side.setFixedWidth(self.SIDEBAR_MIN_WIDTH)
        lay = QVBoxLayout(side)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self.side_buttons: dict[str, QPushButton] = {}
        for index, group in enumerate(self.SIDEBAR_GROUPS):
            if index:
                line = QFrame()
                line.setObjectName("sideSep")
                line.setFixedHeight(1)
                lay.addSpacing(6)
                lay.addWidget(line)
                lay.addSpacing(6)
            for attr in group:
                action: QAction = getattr(self, attr)
                button = QPushButton()
                button.setObjectName("side")
                button.setProperty("primary", attr == "a_connect")  # hebt „Verbinden“ als Hauptaktion hervor
                button.setIconSize(QSize(18, 18))
                button.setFocusPolicy(Qt.FocusPolicy.TabFocus)  # ein Klick nimmt dem Baum den Fokus nicht
                button.setCursor(Qt.CursorShape.PointingHandCursor)
                button.clicked.connect(lambda _=False, a=action: a.trigger())
                action.changed.connect(lambda b=button, a=action: (self._sync_button(b, a), self._fit_to_font()))  # Text kann sich ändern (z. B. Sync)
                self._sync_button(button, action)
                lay.addWidget(button)
                self.side_buttons[attr] = button
        line = QFrame()
        line.setObjectName("sideSep")
        line.setFixedHeight(1)
        lay.addSpacing(6)
        lay.addWidget(line)
        lay.addSpacing(6)
        # Farbschema: drei Umschalter nebeneinander (nur Symbole, damit sie schmal bleiben)
        seg = QWidget()
        seg_lay = QHBoxLayout(seg)
        seg_lay.setContentsMargins(0, 2, 0, 0)
        seg_lay.setSpacing(0)
        self.theme_buttons: dict[str, QPushButton] = {}
        self._theme_group = QButtonGroup(self)
        self._theme_group.setExclusive(True)
        modes = ("light", "dark", "system")
        for i, mode in enumerate(modes):
            b = QPushButton()
            b.setObjectName("seg")
            b.setProperty("pos", "first" if i == 0 else "last" if i == len(modes) - 1 else "middle")
            b.setCheckable(True)
            b.setChecked(self.cfg.theme == mode)
            b.setIconSize(QSize(18, 18))
            b.setFocusPolicy(Qt.FocusPolicy.TabFocus)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setToolTip(tr(self.THEME_TIPS[mode]))
            b.setAccessibleName(tr(self.THEME_TIPS[mode]))
            b.clicked.connect(lambda _=False, m=mode: self.set_theme(m))
            b.toggled.connect(lambda *_: self._refresh_theme_icons())
            self._theme_group.addButton(b)
            seg_lay.addWidget(b, 1)
            self.theme_buttons[mode] = b
        lay.addWidget(seg)
        self.theme_row = seg
        self.side_menu_buttons: dict[str, QPushButton] = {}
        settings_button = QPushButton()  # unter den Umschaltern für das Farbschema
        settings_button.setObjectName("side")
        settings_button.setIconSize(QSize(18, 18))
        settings_button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        settings_button.setCursor(Qt.CursorShape.PointingHandCursor)
        settings_button.clicked.connect(lambda _=False: self.a_settings.trigger())
        self.a_settings.changed.connect(lambda: self._sync_button(settings_button, self.a_settings))
        self._sync_button(settings_button, self.a_settings)
        lay.addSpacing(2)
        lay.addWidget(settings_button)
        self.side_menu_buttons["settings"] = settings_button
        lay.addStretch(1)
        self._outer_layout.addWidget(side)
        self.sidebar = side
        self._fit_to_font()
        # Die Tastenkürzel hingen früher an der Werkzeugleiste: jetzt direkt am Fenster registrieren.
        self.addActions([getattr(self, attr) for group in self.SIDEBAR_GROUPS for attr in group])

    @staticmethod
    def _sync_button(button: QPushButton, action: QAction):
        """Übernimmt Text, Icon und Zustand der Aktion (Icons werden bei Themenwechsel neu gesetzt)."""
        button.setText(action.text())
        button.setIcon(action.icon())
        button.setEnabled(action.isEnabled())
        label = action.text().replace("&", "")
        shortcut = action.shortcut().toString(QKeySequence.SequenceFormat.NativeText)
        button.setToolTip(f"{label} ({shortcut})" if shortcut else label)

    # --- Icons ---
    ACTION_ICONS = (
        ("a_connect", "connect"), ("a_files", "transfer"), ("a_new", "add"), ("a_new_group", "folder_add"), ("a_edit", "edit"),
        ("a_dup", "copy"), ("a_del", "trash"), ("a_sync", "sync"), ("a_sync_settings", "gear"), ("a_identities", "user"), ("a_keys", "key"), ("a_snippets", "snippet"), ("a_terms", "sliders"),
    )

    def _refresh_icons(self):
        """Färbt alle Icons passend zur aktuellen Darstellung (hell/dunkel) ein."""
        c = theme.colors()
        for attr, name in self.ACTION_ICONS:
            action: QAction = getattr(self, attr)
            if attr == "a_connect":  # steht auf der Akzentfläche
                action.setIcon(style.icon(name, c["accent_text"], c["muted"]))
            elif attr == "a_del":
                action.setIcon(style.icon(name, c["danger"], c["muted"]))
            else:
                action.setIcon(style.icon(name, c["text"], c["muted"]))
            action.setIconVisibleInMenu(False)
        self._refresh_theme_icons()
        self.a_settings.setIcon(style.icon("settings", c["text"], c["muted"]))
        if getattr(self, "_search_action", None) is not None:
            self.search.removeAction(self._search_action)
        self._search_action = self.search.addAction(
            style.icon("search", c["muted"]), QLineEdit.ActionPosition.LeadingPosition)
        # Eigener Löschknopf: der eingebaute braucht ein Icon-Theme des Systems und fehlt sonst (z. B. unter Windows).
        if getattr(self, "_clear_action", None) is not None:
            self.search.removeAction(self._clear_action)
        self._clear_action = self.search.addAction(
            style.icon("close", c["muted"]), QLineEdit.ActionPosition.TrailingPosition)
        self._clear_action.setToolTip(tr("Suche leeren"))
        self._clear_action.triggered.connect(self.search.clear)
        self._clear_action.setVisible(bool(self.search.text()))

    def _refresh_theme_icons(self):
        """Das Symbol des aktiven Umschalters steht auf der Akzentfläche und braucht die passende Farbe."""
        c = theme.colors()
        for mode, b in self.theme_buttons.items():
            b.setIcon(style.icon(self.THEME_ICONS[mode], c["accent_text"] if b.isChecked() else c["text"], c["muted"]))

    def _search_text_changed(self, text: str):
        action = getattr(self, "_clear_action", None)
        if action is not None:
            action.setVisible(bool(text))

    def _theme_changed(self):
        self._fit_to_font()
        QTimer.singleShot(0, self._fit_to_font)  # Qt reicht die neue Schrift erst danach an alle Widgets weiter
        self._refresh_icons()
        self.refresh()

    def _fit_to_font(self):
        """Icons und Seitenleiste wachsen mit der Schrift mit, damit nichts abgeschnitten wird."""
        size = max(14, round(18 * theme.scale() / 100))
        self.tree.setIconSize(QSize(size, size))
        buttons = [*self.side_buttons.values(), *self.side_menu_buttons.values()]
        for b in self.theme_buttons.values():
            b.setIconSize(QSize(size, size))
        for button in buttons:
            button.setIconSize(QSize(size, size))
            button.ensurePolished()
        widest = max(b.sizeHint().width() for b in buttons)
        self.sidebar.setFixedWidth(max(self.SIDEBAR_MIN_WIDTH, widest))

    # --- Anzeige ---
    def _terminal_name(self, conn: Connection) -> str:
        t = self.cfg.terminal_by_id(conn.terminal_id) if conn.terminal_id else None
        return t.name if t else tr("Standard")

    def _target(self, conn: Connection) -> str:
        conn, _ = launcher.resolve(self.cfg, conn)
        base = f"{conn.user}@{conn.host}" if conn.user else conn.host
        return f"{base}:{conn.port}" if conn.port != 22 else base

    DEFAULT_WIDTHS = (250, 230, 150, 180)

    def _apply_column_widths(self):
        widths = self.cfg.column_widths
        if len(widths) != len(self.DEFAULT_WIDTHS) or any(w < 30 for w in widths):
            widths = list(self.DEFAULT_WIDTHS)
        header = self.tree.header()
        header.blockSignals(True)
        for i, w in enumerate(widths):
            header.resizeSection(i, w)
        header.blockSignals(False)

    def _flush_widths(self):
        header = self.tree.header()
        widths = [header.sectionSize(i) for i in range(header.count())]
        if widths != self.cfg.column_widths:
            self.cfg.column_widths = widths
            self._save_no_sync()  # reiner Anzeigezustand: kein Abgleich nötig

    def _collapsed_state(self) -> list[str]:
        valid = set(grp.all_groups(self.cfg))
        return sorted(p for p in self.collapsed if p in valid)

    def _flush_collapsed(self):
        """Speichert den Einklapp-Zustand. Bewusst ohne Synchronisation und ohne Fehlerdialog: reiner Anzeigezustand."""
        state = self._collapsed_state()
        if state != self.cfg.collapsed_groups:
            self.cfg.collapsed_groups = state
            try:
                storage.save(self.cfg, self.path)
            except OSError:
                pass

    def _remap_collapsed(self, old: str, new: str):
        """Der Zustand folgt einer umbenannten oder verschobenen Gruppe samt Untergruppen."""
        self.collapsed = {new + c[len(old):] if c == old or c.startswith(old + "/") else c for c in self.collapsed}

    def _restore_window_size(self):
        """Fenstergröße vom letzten Beenden wiederherstellen (nie größer als der Bildschirm)."""
        w, h = self.cfg.window_size if len(self.cfg.window_size) == 2 else (1000, 600)
        screen = self.screen()
        if screen is not None:
            avail = screen.availableGeometry()
            w, h = min(w, avail.width()), min(h, avail.height())
        self.resize(w, h)
        if self.cfg.window_maximized:
            self.setWindowState(self.windowState() | Qt.WindowState.WindowMaximized)

    def _flush_window_size(self):
        """Merkt sich Größe und Maximiert-Zustand. Reiner Anzeigezustand: kein Abgleich, kein Fehlerdialog."""
        maximized = self.isMaximized() or self.isFullScreen()
        size = self.normalGeometry().size() if maximized else self.size()
        new = [size.width(), size.height()]
        if new != self.cfg.window_size or maximized != self.cfg.window_maximized:
            if all(300 <= v <= 20000 for v in new):
                self.cfg.window_size = new
            self.cfg.window_maximized = maximized
            try:
                storage.save(self.cfg, self.path)
            except OSError:
                pass

    def closeEvent(self, event):
        self._flush_window_size()
        for window in list(self._sftp_windows):
            window.close()
        if self._width_timer.isActive():
            self._width_timer.stop()
            self._flush_widths()
        if self._collapse_timer.isActive():
            self._collapse_timer.stop()
            self._flush_collapsed()
        super().closeEvent(event)

    def _expansion_changed(self, item: QTreeWidgetItem, collapsed: bool):
        if self._rebuilding:
            return
        cid = item.data(0, ID_ROLE)
        if cid:
            (self._docker_expanded.discard if collapsed else self._docker_expanded.add)(cid)
            conn = self.cfg.connection_by_id(cid)
            if not collapsed and conn and conn.docker:
                # nicht mitten im Aufklappen am Baum ändern (Qt stürzt sonst ab)
                QTimer.singleShot(0, lambda: self._load_if_needed(cid))
            return
        path = item.data(0, GROUP_ROLE)
        if path is None or self.search.text().strip():
            return
        (self.collapsed.add if collapsed else self.collapsed.discard)(path)
        self._collapse_timer.start()

    def refresh(self, *_):
        needle = self.search.text().strip().lower()
        matches = [
            c
            for c in sorted(self.cfg.connections, key=lambda c: c.name.lower())
            if not needle or needle in " ".join([c.name, c.host, c.user, c.group, *c.tags]).lower()
        ]
        if needle:
            visible: set[str] = set()
            for c in matches:
                visible.update(grp.ancestors(c.group))
            group_paths = sorted(visible, key=str.lower)
        else:
            group_paths = grp.all_groups(self.cfg)

        c = theme.colors()
        def tinted(name: str, key: str, fallback: str):
            return style.icon(name, colors.hex_of(key) or fallback)

        muted = QBrush(QColor(c["muted"]))
        self._rebuilding += 1  # Auf-/Zuklappen während des Aufbaus ist keine Benutzeraktion
        self.tree.clear()
        nodes: dict[str, QTreeWidgetItem] = {}
        for path in group_paths:  # sortiert: Eltern stehen vor Kindern
            item = QTreeWidgetItem([grp.name_of(path)])
            item.setData(0, GROUP_ROLE, path)
            item.setIcon(0, tinted("folder", self.cfg.group_colors.get(path, ""), c["accent"]))
            font = item.font(0)
            font.setBold(True)
            item.setFont(0, font)
            parent = nodes.get(grp.parent_of(path))
            (parent or self.tree.invisibleRootItem()).addChild(item)
            nodes[path] = item
        for conn in matches:
            item = QTreeWidgetItem([conn.name, self._target(conn), self._terminal_name(conn), tag_util.format(conn.tags)])
            item.setData(0, ID_ROLE, conn.id)
            item.setIcon(0, tinted("server", conn.color, c["muted"]))
            item.setForeground(2, muted)
            item.setForeground(3, muted)
            if conn.notes:
                item.setToolTip(0, conn.notes)
            (nodes.get(conn.group) or self.tree.invisibleRootItem()).addChild(item)
            if conn.docker:
                self._fill_containers(item, conn)
                item.setExpanded(conn.id in self._docker_expanded)
        for path, item in nodes.items():
            item.setExpanded(bool(needle) or path not in self.collapsed)
            count = len(grp.connections_in(self.cfg, path))
            item.setText(1, tr("{count} Verbindung(en)", count=count) if count else tr("leer"))
            item.setForeground(1, muted)
        self._rebuilding -= 1
        for cid in self._docker_expanded:  # z. B. nach dem Bearbeiten des Hosts: neu laden, sobald der Baum steht
            QTimer.singleShot(0, lambda cid=cid: self._load_if_needed(cid, retry_errors=False))

    def _selected(self) -> Connection | None:
        item = self.tree.currentItem()
        cid = item.data(0, ID_ROLE) if item else None
        return self.cfg.connection_by_id(cid) if cid else None

    def _selected_connections(self) -> list[Connection]:
        """Alle ausgewählten Verbindungen (Gruppen und Container zählen nicht), in Baumreihenfolge ohne Doppelte."""
        found: list[Connection] = []
        for item in self.tree.selectedItems():
            cid = item.data(0, ID_ROLE)
            conn = self.cfg.connection_by_id(cid) if cid and not item.data(0, CONTAINER_ROLE) else None
            if conn is not None and conn not in found:
                found.append(conn)
        return found

    def _selected_group(self) -> str | None:
        item = self.tree.currentItem()
        return item.data(0, GROUP_ROLE) if item else None

    def _current_group_context(self) -> str:
        """Gruppe, in der neue Elemente standardmäßig landen (aktuelle Auswahl)."""
        group = self._selected_group()
        if group is not None:
            return group
        conn = self._selected()
        if conn is None and (target := self._selected_container()):
            conn = target[0]
        return conn.group if conn else ""

    def _save(self):
        self.cfg.collapsed_groups = self._collapsed_state()  # jedes Speichern nimmt den aktuellen Zustand mit
        try:
            storage.save(self.cfg, self.path)
        except OSError as exc:
            QMessageBox.critical(self, tr("Speichern fehlgeschlagen"), str(exc))
            return
        self.sync.schedule()  # Automatikmodus: verzögert abgleichen

    def _context_menu(self, pos):
        item = self.tree.itemAt(pos)
        if item is not None and not item.isSelected():  # Rechtsklick in eine Auswahl behält sie
            self.tree.setCurrentItem(item)
        multi = self._selected_connections()
        if len(multi) > 1:
            self._multi_context_menu(multi, pos)
            return
        conn = self._selected()
        group = self._selected_group()
        menu = QMenu(self)
        container = self._selected_container()
        if container:
            menu.addAction(self.a_connect)
            sub = _submenu(menu, tr("Verbinden mit …"))
            for t in self.cfg.terminals:
                a = sub.addAction(t.name)
                a.triggered.connect(lambda _=False, term=t: self.connect_container(term))
            menu.addAction(tr("Verbinden als …")).triggered.connect(lambda: self.connect_container(ask=True))
            menu.addSeparator()
            menu.addAction(tr("Container aktualisieren")).triggered.connect(lambda: self.reload_containers(container[0].id))
            menu.addSeparator()
        elif conn:
            menu.addAction(self.a_connect)
            menu.addAction(tr("Als root verbinden (sudo -i)")).triggered.connect(lambda: self.connect_selected(sudo=True))
            sub = _submenu(menu, tr("Verbinden mit …"))
            for t in self.cfg.terminals:
                a = sub.addAction(t.name)
                a.triggered.connect(lambda _=False, term=t: self.connect_selected(term))
            menu.addAction(self.a_files)
            self._snippet_menu(menu, [conn])
            self._color_menu(menu, conn.color, lambda key: self.set_connection_color(conn, key))
            if conn.docker:
                menu.addAction(tr("Container aktualisieren")).triggered.connect(lambda: self.reload_containers(conn.id))
            menu.addSeparator()
            menu.addAction(self.a_edit)
            menu.addAction(self.a_dup)
            menu.addAction(self.a_del)
            menu.addSeparator()
        elif group is not None:
            menu.addAction(tr("Neue Verbindung hier")).triggered.connect(lambda: self.new_connection())
            menu.addAction(tr("Neue Untergruppe …")).triggered.connect(lambda: self.new_group(group))
            menu.addAction(tr("Umbenennen / verschieben …")).triggered.connect(lambda: self.rename_group(group))
            self._color_menu(menu, self.cfg.group_colors.get(group, ""), lambda key: self.set_group_color(group, key))
            menu.addAction(tr("Gruppe löschen")).triggered.connect(lambda: self.delete_group(group))
            menu.addSeparator()
        menu.addAction(self.a_new)
        menu.addAction(tr("Neue Gruppe (oberste Ebene) …")).triggered.connect(lambda: self.new_group(""))
        menu.addAction(self.a_import)
        self._popup(menu, pos)

    def _popup(self, menu: QMenu, pos):
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _multi_context_menu(self, conns: list[Connection], pos):
        menu = QMenu(self)
        menu.addAction(tr("{n} Verbindungen ausgewählt", n=len(conns))).setEnabled(False)
        menu.addSeparator()
        self._snippet_menu(menu, conns)
        shared = conns[0].color if len({c.color for c in conns}) == 1 else ""
        self._color_menu(menu, shared, lambda key: self.set_connections_color(conns, key))
        menu.addSeparator()
        menu.addAction(tr("{n} Verbindungen löschen", n=len(conns))).triggered.connect(lambda: self.delete_connections(conns))
        self._popup(menu, pos)

    def set_connections_color(self, conns: list[Connection], key: str):
        for conn in conns:
            conn.color = key
        self._save()
        self.refresh()
        for conn in conns:  # die Auswahl bleibt für weitere Schritte erhalten
            self._select_connection(conn.id, add=True)

    def _color_menu(self, menu: QMenu, current: str, pick) -> None:
        """Untermenü „Farbe“ mit der Palette; ``pick(schluessel)`` wird mit dem Schlüssel („“ = keine) aufgerufen."""
        sub = _submenu(menu, tr("Farbe"))
        none = sub.addAction(tr("Keine"))
        none.setCheckable(True)
        none.setChecked(not colors.valid(current))
        none.triggered.connect(lambda: pick(""))
        for key, (name, hex_) in colors.PALETTE.items():
            name = tr(name)
            action = sub.addAction(swatch(hex_), name)
            action.setCheckable(True)
            action.setChecked(key == current)
            action.triggered.connect(lambda _=False, k=key: pick(k))

    def set_connection_color(self, conn: Connection, key: str):
        conn.color = key
        self._save()
        self.refresh()
        self._select_connection(conn.id)

    def set_group_color(self, path: str, key: str):
        grp.set_color(self.cfg, path, key)
        self._save()
        self.refresh()

    def _double_clicked(self, item=None, *_):
        if item is not None and item.data(0, GROUP_ROLE) is not None:
            item.setExpanded(not item.isExpanded())
        elif self._selected() or self._selected_container():
            self.connect_selected()

    # --- Docker-Container ---
    def _find_item(self, conn_id: str) -> QTreeWidgetItem | None:
        it = QTreeWidgetItemIterator(self.tree)
        while it.value():
            if it.value().data(0, ID_ROLE) == conn_id:
                return it.value()
            it += 1
        return None

    def _selected_container(self) -> tuple[Connection, str] | None:
        item = self.tree.currentItem()
        data = item.data(0, CONTAINER_ROLE) if item else None
        cid, _sep, name = data.partition("\x1f") if data else ("", "", "")
        conn = self.cfg.connection_by_id(cid) if cid else None
        return (conn, name) if conn else None

    def _info_item(self, text: str, tooltip: str = "") -> QTreeWidgetItem:
        item = QTreeWidgetItem([text])
        item.setForeground(0, QBrush(QColor(theme.colors()["muted"])))
        font = item.font(0)
        font.setItalic(True)
        item.setFont(0, font)
        if tooltip:
            item.setToolTip(0, tooltip)
        return item

    def _fill_containers(self, host_item: QTreeWidgetItem, conn: Connection):
        """Hängt die Unterelemente eines Docker-Hosts passend zum Ladezustand an (nur an frisch erzeugte Elemente)."""
        self._add_container_children(host_item, conn)

    def _add_container_children(self, host_item: QTreeWidgetItem, conn: Connection):
        c = theme.colors()
        icon = style.icon("box", c["accent"])
        muted = QBrush(QColor(c["muted"]))
        state = self._containers.get(conn.id, "")
        if conn.id not in self._containers:
            host_item.addChild(self._info_item("…", tr("Aufklappen, um die Container zu laden")))
        elif state is None:
            host_item.addChild(self._info_item(tr("Container werden geladen …")))
        elif isinstance(state, str):
            host_item.addChild(self._info_item("⚠ " + state, state))
        elif not state:
            host_item.addChild(self._info_item(tr("Keine laufenden Container")))
        else:
            for box in state:
                child = QTreeWidgetItem([box.name, f"{box.image} · {box.status}" if box.status else box.image, ""])
                child.setData(0, CONTAINER_ROLE, f"{conn.id}\x1f{box.name}")
                child.setIcon(0, icon)
                child.setForeground(1, muted)
                child.setToolTip(0, "\n".join(filter(None, [box.name, box.image, box.status, box.ports])))
                child.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)  # nicht verschiebbar
                host_item.addChild(child)

    def _current_key(self) -> tuple | None:
        item = self.tree.currentItem()
        if item is None:
            return None
        if item.data(0, CONTAINER_ROLE):
            return ("container", item.data(0, CONTAINER_ROLE))
        if item.data(0, ID_ROLE):
            return ("conn", item.data(0, ID_ROLE))
        if item.data(0, GROUP_ROLE) is not None:
            return ("group", item.data(0, GROUP_ROLE))
        return None

    def _restore_key(self, key: tuple | None) -> bool:
        if key is None:
            return False
        role = {"container": CONTAINER_ROLE, "conn": ID_ROLE, "group": GROUP_ROLE}[key[0]]
        it = QTreeWidgetItemIterator(self.tree)
        while it.value():
            if it.value().data(0, role) == key[1]:
                self.tree.setCurrentItem(it.value())
                return True
            it += 1
        return False

    def _refill(self, conn_id: str):
        """Zeigt geänderte Container an. Der Baum wird dazu ganz neu aufgebaut (Auswahl und Scrollposition bleiben):
        einzelne Unterelemente auszutauschen, brachte Qt/PySide nach einem früheren Neuaufbau zum Absturz."""
        if self._find_item(conn_id) is None:
            return
        key, bar = self._current_key() or self._lost_key, self.tree.verticalScrollBar()
        scroll = bar.value()
        self.refresh()
        found = self._restore_key(key)
        # Ein gewählter Container verschwindet beim Neuladen kurz; die Auswahl kehrt zurück, sobald es ihn wieder gibt.
        self._lost_key = key if key and key[0] == "container" and not found and self._containers.get(conn_id) is None else None
        bar.setValue(scroll)

    def _load_containers(self, conn: Connection):
        gen = self._docker_gen[conn.id] = self._docker_gen.get(conn.id, 0) + 1
        self._containers[conn.id] = None
        self._refill(conn.id)
        snapshot = replace(conn)
        password = self._docker_passwords.get(conn.id)

        def work(job):
            try:
                found = docker.list_containers(self.cfg, snapshot, password=password)
            except Exception as exc:  # jede Meldung landet als Text im Baum
                return job, conn.id, gen, None, exc
            return job, conn.id, gen, found, None

        job = Job(work, self)
        self._docker_jobs.add(job)
        job.result.connect(self._containers_loaded)
        job.start()

    @Slot(object)
    def _containers_loaded(self, res):
        job, cid, gen, found, exc = res
        self._docker_jobs.discard(job)
        if gen != self._docker_gen.get(cid):
            return  # inzwischen neu geladen oder Verbindung geändert
        conn = self.cfg.connection_by_id(cid)
        if conn is None or not conn.docker:
            self._containers.pop(cid, None)
            return
        if isinstance(exc, sftp.SftpAuthError):
            typed = self._docker_passwords.pop(cid, None) is not None
            if not typed:  # kein gespeichertes Passwort / kein Schlüssel hat funktioniert: einmal nachfragen
                text, ok = QInputDialog.getText(
                    self, tr("Anmeldung"), tr("Passwort für {v}{host}:", v=conn.user + '@' if conn.user else '', host=conn.host),
                    QLineEdit.EchoMode.Password)
                if ok and text:
                    self._docker_passwords[cid] = text
                    self._load_containers(conn)
                    return
        if exc is not None:
            self._containers[cid] = str(exc) or type(exc).__name__
        else:
            self._containers[cid] = found
        self._refill(cid)

    def _docker_forget(self, conn_id: str):
        self._containers.pop(conn_id, None)
        self._docker_gen[conn_id] = self._docker_gen.get(conn_id, 0) + 1  # laufende Abfragen verwerfen
        self._docker_passwords.pop(conn_id, None)

    def _load_if_needed(self, conn_id: str, retry_errors: bool = True):
        """Lädt die Container eines aufgeklappten Hosts, falls noch nie geladen (oder der letzte Versuch fehlschlug)."""
        conn = self.cfg.connection_by_id(conn_id)
        state = self._containers.get(conn_id, "")
        missing = conn_id not in self._containers or (retry_errors and isinstance(state, str))
        if conn and conn.docker and conn_id in self._docker_expanded and missing:
            self._load_containers(conn)

    def reload_containers(self, conn_id: str):
        conn = self.cfg.connection_by_id(conn_id)
        item = self._find_item(conn_id)
        if conn is None or item is None or not conn.docker:
            return
        self._docker_expanded.add(conn_id)
        self._rebuilding += 1
        item.setExpanded(True)
        self._rebuilding -= 1
        self._load_containers(conn)

    def connect_container(self, terminal: Terminal | None = None, ask: bool = False):
        """Öffnet eine Shell im gewählten Container. ``ask``: Shell und Benutzer vorher abfragen."""
        target = self._selected_container()
        if not target:
            return
        conn, name = target
        shell = user = None  # None = Vorgaben der Verbindung
        if ask:
            dlg = DockerConnectDialog(name, conn.docker_shell, conn.docker_user, self)
            if not dlg.exec():
                return
            shell, user = dlg.values()
        try:
            titled, command = docker.container_target(conn, name, shell, user)
            launcher.launch(self.cfg, titled, terminal, remote_command=command)
        except (docker.DockerError, launcher.LaunchError) as exc:
            QMessageBox.critical(self, tr("Verbindung konnte nicht geöffnet werden"), str(exc))

    # --- Verbindungen ---
    def open_files(self):
        """Öffnet den Dateibrowser (SFTP) für die gewählte Verbindung."""
        conn = self._selected()
        if not conn:
            return
        window = SftpWindow(self.cfg, conn)
        self._sftp_windows.append(window)
        window.closed.connect(lambda w: self._sftp_windows.remove(w) if w in self._sftp_windows else None)
        window.show()

    def connect_selected(self, terminal: Terminal | None = None, sudo: bool = False):
        conn = self._selected()
        if not conn:
            if self._selected_container():
                self.connect_container(terminal)
            return
        if sudo:  # einmalig, wird nicht gespeichert
            conn = replace(conn, sudo_mode=conn.sudo_mode or "login")
        try:
            launcher.launch(self.cfg, conn, terminal)
        except launcher.LaunchError as exc:
            QMessageBox.critical(self, tr("Verbindung konnte nicht geöffnet werden"), str(exc))

    def _apply_password(self, conn: Connection, change: str | None) -> None:
        if change is None:
            return
        try:
            passwords.apply_change(conn, change)
        except passwords.PasswordError as exc:
            QMessageBox.critical(self, tr("Passwort"), str(exc))

    def new_connection(self):
        dlg = ConnectionDialog(self.cfg, parent=self, default_group=self._current_group_context(), save_callback=self._save)
        if dlg.exec():
            conn = dlg.result_connection()
            self._apply_password(conn, dlg.password_change)
            self.cfg.connections.append(conn)
            self._save()
            self.refresh()

    def edit_selected(self, select_name: bool = False):
        group = self._selected_group()
        if group is not None:
            self.rename_group(group)
            return
        conn = self._selected()
        if not conn:
            return
        dlg = ConnectionDialog(self.cfg, conn, parent=self, save_callback=self._save)
        if select_name:
            dlg.name.selectAll()  # der Name lässt sich sofort überschreiben
        if dlg.exec():
            self._apply_password(conn, dlg.password_change)
            self._docker_forget(conn.id)  # Zugang oder Docker-Einstellungen können sich geändert haben
            self._save()
            self.refresh()

    def duplicate_selected(self):
        conn = self._selected()
        if not conn:
            return
        clone = copy.deepcopy(conn)
        clone.id = new_id()
        clone.name = tr("{name} (Kopie)", name=conn.name)
        if conn.use_password:
            try:
                pw = passwords.get_password(conn.id)
                if pw is not None:
                    passwords.set_password(clone.id, pw)
                else:
                    clone.use_password = False
            except passwords.PasswordError as exc:
                clone.use_password = False
                QMessageBox.warning(self, tr("Passwort nicht kopiert"), str(exc))
        self.cfg.connections.append(clone)
        self._save()
        self.refresh()
        # Die Kopie gleich zum Bearbeiten öffnen; bricht man ab, bleibt sie (ausgewählt) erhalten.
        if self._select_connection(clone.id):
            self.edit_selected(select_name=True)
            self._select_connection(clone.id)  # das Speichern baut den Baum neu auf und verliert die Auswahl

    def _select_connection(self, conn_id: str, add: bool = False) -> bool:
        """Wählt die Verbindung im Baum aus (klappt übergeordnete Gruppen auf). Liefert False, wenn sie nicht sichtbar ist."""
        it = QTreeWidgetItemIterator(self.tree)
        while it.value():
            item = it.value()
            if item.data(0, ID_ROLE) == conn_id:
                parent = item.parent()
                while parent is not None:
                    parent.setExpanded(True)
                    parent = parent.parent()
                if add:
                    item.setSelected(True)
                else:
                    self.tree.setCurrentItem(item)
                    self.tree.scrollToItem(item)
                return True
            it += 1
        return False

    def delete_selected(self):
        group = self._selected_group()
        if group is not None:
            self.delete_group(group)
            return
        conns = self._selected_connections() if len(self.tree.selectedItems()) > 1 else []
        if not conns:
            conn = self._selected()
            conns = [conn] if conn else []
        if conns:
            self.delete_connections(conns)

    def delete_connections(self, conns: list[Connection]):
        if len(conns) == 1:
            text = tr("Verbindung „{name}“ wirklich löschen?", name=conns[0].name)
        else:
            names = "\n".join(f"• {c.name}" for c in conns[:10])
            more = tr("\n… und {v} weitere", v=len(conns) - 10) if len(conns) > 10 else ""
            text = tr("{n} Verbindungen wirklich löschen?\n\n{names}{more}", n=len(conns), names=names, more=more)
        if QMessageBox.question(self, tr("Löschen"), text) != QMessageBox.StandardButton.Yes:
            return
        failed: list[str] = []
        for conn in conns:
            if conn.use_password:
                try:
                    passwords.delete_password(conn.id)
                except passwords.PasswordError as exc:
                    failed.append(f"{conn.name}: {exc}")
            if conn in self.cfg.connections:
                self.cfg.connections.remove(conn)
            self._docker_forget(conn.id)
            self._docker_expanded.discard(conn.id)
        self._save()
        self.refresh()
        if failed:
            QMessageBox.warning(self, tr("Passwort nicht gelöscht"), "\n".join(failed))

    # --- Gruppen ---
    def new_group(self, parent: str | None = None):
        if parent is None:
            parent = self._current_group_context()
        title = tr("Neue Untergruppe in „{parent}“", parent=parent) if parent else tr("Neue Gruppe")
        name, ok = QInputDialog.getText(self, title, tr("Name der Gruppe:"))
        if not ok or not name.strip():
            return
        try:
            path = grp.add_group(self.cfg, grp.join(parent, name))
        except ValueError as exc:
            QMessageBox.warning(self, tr("Gruppe"), str(exc))
            return
        self.collapsed.difference_update(grp.ancestors(path))
        self._save()
        self.refresh()

    def rename_group(self, path: str):
        new, ok = QInputDialog.getText(
            self, tr("Gruppe umbenennen / verschieben"), tr("Neuer Pfad (Untergruppen mit „/“ trennen):"), text=path
        )
        if not ok or not new.strip():
            return
        try:
            grp.rename_group(self.cfg, path, new)
        except ValueError as exc:
            QMessageBox.warning(self, tr("Gruppe"), str(exc))
            return
        self._remap_collapsed(path, grp.normalize(new))
        self._save()
        self.refresh()

    def delete_group(self, path: str):
        n = len(grp.connections_in(self.cfg, path))
        parent = grp.parent_of(path)
        target = f"„{parent}“" if parent else tr("die oberste Ebene")
        answer = QMessageBox.question(
            self,
            tr("Gruppe löschen"),
            tr("Gruppe „{path}“ löschen?\nEnthaltene Verbindungen ({n}) und Untergruppen werden nach {target} verschoben, keine Verbindung wird gelöscht.", path=path, n=n, target=target),
        )
        if answer == QMessageBox.StandardButton.Yes:
            grp.delete_group(self.cfg, path)
            self.collapsed = {  # Untergruppen rücken eine Ebene hoch, die gelöschte Gruppe selbst entfällt
                grp.join(parent, c[len(path) + 1:]) if c.startswith(path + "/") else c for c in self.collapsed if c != path}
            self._save()
            self.refresh()

    def _move(self, kind: str, key: str, target: str):
        """Drag & Drop: Verbindung oder Gruppe in die Zielgruppe verschieben."""
        if kind == "conn":
            conn = self.cfg.connection_by_id(key)
            if conn is None or conn.group == target:
                return
            conn.group = target
        else:
            if target == key or grp.parent_of(key) == target:
                return
            new_path = grp.join(target, grp.name_of(key))
            try:
                grp.rename_group(self.cfg, key, new_path)
            except ValueError as exc:
                QMessageBox.warning(self, tr("Gruppe"), str(exc))
                return
            self._remap_collapsed(key, grp.normalize(new_path))
        if target:
            self.collapsed.difference_update(grp.ancestors(target))
        self._save()
        self.refresh()

    # --- Sonstiges ---
    def edit_terminals(self):
        dlg = TerminalsDialog(self.cfg, self)
        if dlg.exec():
            self.cfg.terminals, self.cfg.default_terminal_id = dlg.result_values()
            valid = {t.id for t in self.cfg.terminals}
            for c in self.cfg.connections:
                if c.terminal_id and c.terminal_id not in valid:
                    c.terminal_id = ""
            self._save()
            self.refresh()

    # --- Synchronisation ---
    def _sync_status(self, text: str, error: bool):
        self.sync_label.setText(("⚠ " if error else "⟳ " if text.endswith("…") else "✓ ") + text)
        self.sync_label.setToolTip(text)

    def _sync_changed_config(self):
        try:
            storage.save(self.cfg, self.path)  # ohne erneuten Abgleich auszulösen
        except OSError:
            pass
        self.refresh()

    def _sync_finished(self, report, error, manual: bool):
        if error and manual:
            QMessageBox.warning(self, tr("Synchronisation fehlgeschlagen"), str(error))
        elif report is not None and report.conflicts:
            QMessageBox.information(
                self,
                tr("Synchronisation"),
                tr("Es gab widersprüchliche Änderungen an: {names}.\nDeine Version blieb erhalten, die andere steht als „… (Konflikt)“ daneben.",
                   names=", ".join(report.conflicts)),
            )

    def edit_sync(self):
        dlg = SyncDialog(self.cfg, self)
        if dlg.exec():
            self._save_no_sync()
            if self.sync.enabled():
                self.sync.sync(manual=True)

    def set_font_scale(self, percent: int):
        percent = theme.clamp_scale(percent)
        app = QApplication.instance()
        if app is not None:
            percent = theme.apply_scale(app, percent)
        if percent != self.cfg.font_scale:
            self.cfg.font_scale = percent
            self._save_no_sync()  # reiner Anzeigezustand: kein Abgleich nötig

    def set_language(self, code: str):
        """Merkt die Sprache; sie wird beim nächsten Start angewandt (die Fenster sind bereits aufgebaut)."""
        self.cfg.language = i18n.normalize(code)
        self._save_no_sync()

    def edit_settings(self):
        dlg = SettingsDialog(self.cfg.font_scale, self.cfg.language, self.set_font_scale, self.set_language, self)
        dlg.exec()

    def set_theme(self, mode: str):
        self.cfg.theme = mode
        app = QApplication.instance()
        if app is not None:
            theme.apply(app, mode)
        self._save_no_sync()

    def _save_no_sync(self):
        self.cfg.collapsed_groups = self._collapsed_state()
        try:
            storage.save(self.cfg, self.path)
        except OSError as exc:
            QMessageBox.critical(self, tr("Speichern fehlgeschlagen"), str(exc))

    def edit_snippets(self):
        preselect = [c.id for c in self._selected_connections()]
        SnippetsDialog(self.cfg, self._save, self.run_snippet, preselect, self).exec()
        self.refresh()

    def run_snippet(self, snippet, conns: list[Connection]):
        """Führt das Snippet im Terminal aus, je Verbindung ein Fenster. Bei mehreren Zielen wird vorher nachgefragt."""
        if len(conns) > snippets.MAX_HOSTS_WITHOUT_WARNING:
            names = "\n".join(f"• {c.name}" for c in conns[:10]) + (tr("\n… und {v} weitere", v=len(conns) - 10) if len(conns) > 10 else "")
            text = tr("Snippet „{name}“ auf {n} Verbindungen ausführen?\nEs öffnen sich {n} Terminals.\n\n{names}", name=snippet.name, n=len(conns), names=names)
            if QMessageBox.question(self, tr("Snippet ausführen"), text) != QMessageBox.StandardButton.Yes:
                return
        report = snippets.run(self.cfg, snippet, conns)
        if report.errors:
            lines = "\n".join(f"{name + ': ' if name else ''}{msg}" for name, msg in report.errors)
            QMessageBox.warning(self, tr("Snippet"), tr("Nicht alle Terminals konnten gestartet werden:\n\n{lines}", lines=lines))

    def _snippet_menu(self, menu: QMenu, conns: list[Connection]) -> None:
        """Untermenü „Snippet ausführen“ mit den Kategorien als Unterordner."""
        sub = _submenu(menu, tr("Snippet ausführen"))
        sub.setEnabled(bool(self.cfg.snippets))
        submenus: dict[str, QMenu] = {"": sub}

        def holder(category: str) -> QMenu:
            if category not in submenus:
                submenus[category] = _submenu(holder(grp.parent_of(category)), grp.name_of(category))
            return submenus[category]

        for cat in snippets.categories(self.cfg):
            holder(cat)
        for cat in ["", *snippets.categories(self.cfg)]:
            for sn in snippets.in_category(self.cfg, cat):
                action = holder(cat).addAction(sn.name)
                action.setToolTip(sn.command)
                action.triggered.connect(lambda _=False, s=sn, c=list(conns): self.run_snippet(s, c))
        if not self.cfg.snippets:
            sub.setToolTip(tr("Noch keine Snippets angelegt"))

    def edit_keys(self):
        KeysDialog(self.cfg, self._save, self).exec()
        self.refresh()

    def edit_identities(self):
        IdentitiesDialog(self.cfg, self._save, self).exec()
        self.refresh()

    def import_ssh_config(self):
        found = parse_ssh_config()
        known = {(c.host, c.user, c.port, c.name) for c in self.cfg.connections}
        new = [c for c in found if (c.host, c.user, c.port, c.name) not in known]
        if not new:
            QMessageBox.information(self, tr("Import"), tr("Keine neuen Einträge in ~/.ssh/config gefunden."))
            return
        self.cfg.connections.extend(new)
        self._save()
        self.refresh()
        QMessageBox.information(self, tr("Import"), tr("{n} Verbindung(en) importiert (Gruppe „ssh-config“).", n=len(new)))
