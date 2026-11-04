from __future__ import annotations

import copy
import time
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QAction, QActionGroup, QBrush, QColor, QGuiApplication, QIcon, QKeySequence, QPainter
from PySide6.QtWidgets import (
    QTabBar, QTabWidget, QAbstractItemView, QApplication, QButtonGroup, QFrame, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QMainWindow, QMenu,
    QMessageBox, QPushButton, QToolButton,
    QTreeWidget, QTreeWidgetItem, QTreeWidgetItemIterator, QVBoxLayout, QWidget,
)

from .. import __version__
from .. import i18n
from ..i18n import tr
from .. import groups as grp
from . import safe, style, theme, type_badges
from ..clipboard_history import History as ClipboardHistory
from .. import colors, docker, duplicates, cisco, launcher, localshell, osinfo, passwords, proxmox, runtime, sftp, snippets, remote, storage, tunnels, wireguard
from .. import tags as tag_util
from ..models import COLUMN_KEYS, Config, Connection, Terminal, new_id
from ..ssh_config import parse_ssh_config
from .clipboard_dialog import ClipboardDialog, ClipboardPopup, confirm_paste
from .connection_dialog import ConnectionDialog, swatch
from .docker_dialog import DockerConnectDialog
from .hostkey import HostKeyAsker
from .identities_dialog import IdentitiesDialog
from .keys_dialog import KeysDialog
from .settings_dialog import SettingsDialog
from .quick_dialog import QuickLauncher
from .snippets_dialog import SnippetsDialog
from .jobs import Job
from .sftp_window import SftpWindow
from .sync_conflict_dialog import SyncConflictDialog
from .sync_controller import SyncController
from .sync_dialog import SyncDialog
from .terminal_tab import TerminalTab
from .terminal_view import TerminalView, available as terminal_available
from .terminals_dialog import TerminalsDialog
from .toast import ToastHost
from .viewers_dialog import ViewersDialog
from .wireguard_indicator import WireGuardIndicator

ID_ROLE = int(Qt.ItemDataRole.UserRole)  # Verbindungs-ID
GROUP_ROLE = ID_ROLE + 1  # Gruppenpfad
CONTAINER_ROLE = ID_ROLE + 2  # „<ID der Host-Verbindung>\x1f<Containername bzw. Proxmox-VMID>“
OPEN_ROLE = ID_ROLE + 4  # Verbindung ist gerade in einem Terminal offen (grüner Punkt)
OPEN_COLOR = "#2ecc71"
FAV_ROLE = ID_ROLE + 3  #„head“ beim Favoriten-Kopf, „entry“ bei einer Verbindung in diesem Abschnitt (Verweis, nicht die Verbindung selbst)
GOLD = "#e8a317"  # Stern der Favoriten


TYPE_FILTERS = ("ssh", "vnc", "rdp")


def _split_type_filter(needle: str) -> tuple[set[str], str]:
    """Trennt „typ:vnc“ (auch mehrfach) vom übrigen Suchtext; ohne gültigen Filter bleibt der Text unverändert."""
    wanted: set[str] = set()
    rest: list[str] = []
    for token in needle.split():
        if token.startswith("typ:") and token[4:] in TYPE_FILTERS:
            wanted.add(token[4:])
        else:
            rest.append(token)
    return (wanted, " ".join(rest)) if wanted else (set(), needle)


def _has_children(conn: Connection) -> bool:
    """Der Host zeigt Docker-Container, Proxmox-Gäste oder Cisco-Ports als Unterelemente."""
    return conn.docker or conn.proxmox or conn.cisco


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
        if source is None or source.data(0, FAV_ROLE) == "head":
            return
        if target is not None and target.data(0, FAV_ROLE) is not None:
            return  # der Favoriten-Abschnitt ist keine Gruppe: nichts dorthin verschieben
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
        self._hostkey_asker = HostKeyAsker(self)
        sftp.host_key_confirmer = self._hostkey_asker.confirm  # unbekannte Host-Schlüssel nur nach Bestätigung merken
        self._sftp_windows: list[SftpWindow] = []  # offene Dateibrowser (SFTP)
        self._fav_collapsed = False  # Favoriten-Abschnitt zugeklappt (nur zur Laufzeit)
        self.tunnels = tunnels.Manager()  # laufende Port-Weiterleitungen (SSH-Tunnel)
        self.remote = remote.Manager(self.tunnels)  # offene Sitzungen des Fernzugriffs (VNC/RDP; Viewer, bei Bedarf mit kurzlebigem Tunnel)
        self.remote.open_builtin = self._open_builtin_viewer
        self._tunnel_up: set[tuple[str, str]] = set()  # Tunnel, deren „aktiv“ schon gemeldet wurde
        self._tunnel_sig: tuple = ()
        self._tunnel_timer = QTimer(self)
        self._tunnel_timer.setInterval(1000)
        self._tunnel_timer.timeout.connect(self._poll_tunnels)
        self._open_timer = QTimer(self)  # grüner Punkt bei Verbindungen, die in einem Terminal offen sind
        self._open_timer.setInterval(2000)
        self._open_timer.timeout.connect(self._update_open_markers)
        self._open_timer.start()
        # Docker-Container je Host (nur zur Laufzeit): ID → Liste, Fehlertext oder None (= wird geladen)
        self._containers: dict[str, list[docker.Container] | str | None] = {}
        self._docker_gen: dict[str, int] = {}  # Zähler, damit veraltete Antworten verworfen werden
        self._docker_expanded: set[str] = set()  # aufgeklappte Hosts
        self._docker_passwords: dict[str, str] = {}  # nachgefragte Passwörter, nur im Speicher
        self._docker_jobs: set[Job] = set()
        self._lost_key: tuple | None = None
        self._os_jobs: set[Job] = set()
        self._os_busy: set[str] = set()  # Verbindungen, deren Betriebssystem gerade abgefragt wird
        self._os_tried: set[str] = set()  # in dieser Sitzung erfolglos abgefragt: nicht erneut versuchen
        self._os_passwords: dict[str, str] = {}  # nachgefragte Passwörter, nur im Speicher
        self._rebuilding = 0  # >0, solange der Baum programmatisch umgebaut wird
        # zugeklappte Gruppen: aus der Konfiguration wiederherstellen, Verwaistes (gelöschte Gruppen) verwerfen
        self.collapsed: set[str] = set(cfg.collapsed_groups) & set(grp.all_groups(cfg))
        self.setWindowTitle("Sesam")
        self._restore_window_size()

        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("Suchen (Name, Host, Benutzer, Gruppe) …"))
        self.search.setToolTip(tr("Tipp: „typ:vnc“, „typ:rdp“ oder „typ:ssh“ zeigt nur diese Verbindungen."))
        self.search.textChanged.connect(self.refresh)
        self.search.textChanged.connect(self._search_text_changed)

        self.tree = ConnectionTree()
        self.tree.setHeaderLabels([tr("Name"), tr("Ziel"), tr("Terminal"), tr("Tags"), tr("Betriebssystem"), tr("Typ")])
        self.tree.setAlternatingRowColors(False)
        self.tree.setIndentation(22)
        self.tree.setUniformRowHeights(True)
        self.tree.setExpandsOnDoubleClick(False)  # Doppelklick verbindet; Gruppen klappen wir selbst auf
        self.tree.setIconSize(QSize(18, 18))
        header = self.tree.header()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)  # Breite frei ziehbar
        header.setStretchLastSection(True)
        header.setMinimumSectionSize(60)
        header.moveSection(4, 2)  # Betriebssystem steht optisch hinter dem Ziel (logische Spalte bleibt 4)
        header.moveSection(5, 1)  # der Typ (SSH, VNC, RDP) steht optisch direkt hinter dem Namen (logische Spalte bleibt 5)
        self.tree.setItemDelegateForColumn(5, type_badges.TypeBadgeDelegate(self.tree))
        self._apply_column_widths()
        self._apply_column_visibility()
        header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        header.customContextMenuRequested.connect(self._header_menu)
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
        # Reiter: vorn die Verbindungsliste, dahinter die Sitzungen des integrierten Terminals (ohne Sitzung blendet Qt die Leiste aus)
        self.tabs = QTabWidget()
        self.tabs.setObjectName("mainTabs")
        self.tabs.setDocumentMode(True)
        self.tabs.setTabsClosable(True)
        self.tabs.setTabBarAutoHide(True)
        self.tabs.addTab(central, tr("Verbindungen"))
        for side in (QTabBar.ButtonPosition.LeftSide, QTabBar.ButtonPosition.RightSide):
            self.tabs.tabBar().setTabButton(0, side, None)  # der erste Reiter lässt sich nicht schließen
        self.tabs.tabCloseRequested.connect(self._close_terminal_tab)
        self.tabs.currentChanged.connect(self._tab_changed)
        self._build_tile_button()
        self.setCentralWidget(self.tabs)
        self._split_target: tuple[TerminalView, Qt.Orientation] | None = None  # nächste Sitzung als Kachel neben/unter dieser öffnen
        self._recent_terminal_tab: TerminalTab | None = None  # zuletzt benutzter Terminal-Reiter (Ziel für „Neben/Unter dem aktuellen Terminal öffnen“)
        launcher.set_embedded_opener(self._open_embedded)
        self._init_clipboard()

        self.sync = SyncController(self.cfg, lambda: storage.save(self.cfg, self.path), self, resolver=self._ask_sync_conflicts)
        self.sync.status.connect(self._sync_status)
        self.sync.cfg_changed.connect(self._sync_changed_config)
        self.sync.finished.connect(self._sync_finished)
        self.toasts = ToastHost(self, lambda: self.cfg.toasts)  # Meldungen: Einblendung oder Statuszeile (Einstellung)
        self.sync_label = safe.plain(QLabel(""))
        self.statusBar().addPermanentWidget(self.sync_label)
        self.sync_label.setVisible(not self.cfg.toasts)
        self.wg_indicator = WireGuardIndicator(self)  # ganz rechts: Zustand des WireGuard-Tunnels
        self.statusBar().addPermanentWidget(self.wg_indicator)
        self.version_label = QLabel(tr("Version {__version__}", __version__=__version__))  # ganz links in der Statuszeile
        self.version_label.setObjectName("versionLabel")
        self.version_label.setToolTip("Sesam")
        self.version_label.setContentsMargins(12, 0, 0, 0)  # bündig mit der Verbindungsliste
        self.statusBar().addWidget(self.version_label)
        self.statusBar().setSizeGripEnabled(False)
        self._tunnel_timer.start()

        self._build_actions()
        self.refresh()
        if self.cfg.wireguard:  # standardmäßig aus: dann wird nichts abgefragt und kein Passwort gelesen
            self.wg_indicator.set_elevation(self._stored_elevation())
        self.wg_indicator.set_enabled(self.cfg.wireguard)
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
        self.a_quick = act(tr("Schnellstarter"), self.open_quick, "Ctrl+K")
        self.a_files = act(tr("Dateien (SFTP)"), self.open_files, "F4")
        self.a_local = act(tr("Lokales Terminal"), self.open_local_terminal, "Ctrl+Shift+T")
        self.a_new = act(tr("Neue Verbindung"), self.new_connection, "Ctrl+N")
        self.a_new_group = act(tr("Neue Gruppe …"), self.new_group, "Ctrl+Shift+N")
        self.a_fav = act(tr("Favorit"), self.toggle_favorite_selected, "Ctrl+B")
        self.a_fav.setEnabled(self.cfg.favorites)  # in den Einstellungen abschaltbar
        self.a_edit = act(tr("Bearbeiten"), self.edit_selected, "F2")
        self.a_dup = act(tr("Duplizieren"), self.duplicate_selected, "Ctrl+D")
        self.a_del = act(tr("Löschen"), self.delete_selected, "Del")
        self.a_terms = act(tr("Terminals …"), self.edit_terminals)
        self.a_viewers = act(tr("Viewer …"), self.edit_viewers)
        self.a_identities = act(tr("Identitäten …"), self.edit_identities)
        self.a_keys = act(tr("Schlüssel …"), self.edit_keys)
        self.a_snippets = act(tr("Snippets …"), self.edit_snippets, "Ctrl+Shift+S")
        self.a_clipboard = act(tr("Zwischenablage …"), self.open_clipboard)
        self.a_sync = act(tr("Jetzt synchronisieren"), lambda: self.sync.sync(manual=True), "F5")
        self.a_sync_settings = act(tr("Synchronisation …"), self.edit_sync)
        self.a_import = act(tr("Aus ~/.ssh/config importieren"), self.import_ssh_config)
        self.a_dedupe = act(tr("Doppelte Verbindungen zusammenführen …"), self.merge_duplicates)

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
        ("a_connect", "a_quick", "a_files", "a_local", "a_new", "a_new_group", "a_fav", "a_edit", "a_dup", "a_del"),
        ("a_sync", "a_identities", "a_keys", "a_snippets", "a_clipboard"),
    )

    def _build_sidebar(self):
        """Alle Hauptaktionen untereinander in einer Leiste am rechten Rand."""
        side = QWidget()
        side.setObjectName("sidebar")
        side.setFixedWidth(self.SIDEBAR_MIN_WIDTH)
        lay = QVBoxLayout(side)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)  # eng gesetzt: mit jedem Knopf wächst die Mindesthöhe des Fensters (Laptop-Bildschirme mit 768 Pixeln)
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
        ("a_connect", "connect"), ("a_quick", "search"), ("a_fav", "star"), ("a_files", "transfer"), ("a_local", "terminal"), ("a_new", "add"), ("a_new_group", "folder_add"), ("a_edit", "edit"),
        ("a_dup", "copy"), ("a_del", "trash"), ("a_sync", "sync"), ("a_sync_settings", "gear"), ("a_identities", "user"), ("a_keys", "key"), ("a_snippets", "snippet"), ("a_clipboard", "clipboard"), ("a_terms", "sliders"), ("a_viewers", "monitor"),
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
        for view in self._terminal_views():
            view.set_dark(theme.is_dark())
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
        if conn.remote and conn.remote_mode == "direct":  # Gerät ohne SSH: das Ziel ist der VNC- bzw. RDP-Anschluss
            return f"{conn.remote}://{conn.host}:{remote.port_of(conn)}"
        conn, _ = launcher.resolve(self.cfg, conn)
        base = f"{conn.user}@{conn.host}" if conn.user else conn.host
        return f"{base}:{conn.port}" if conn.port != 22 else base

    DEFAULT_WIDTHS = (250, 230, 150, 180, 200, 120)
    COLUMN_INDEX = {"target": 1, "terminal": 2, "tags": 3, "os": 4, "type": 5}

    def _apply_column_widths(self):
        widths = list(self.cfg.column_widths)
        if 4 <= len(widths) < len(self.DEFAULT_WIDTHS):  # Konfiguration aus der Zeit vor der Betriebssystem- bzw. Typ-Spalte
            widths += self.DEFAULT_WIDTHS[len(widths):]
        if len(widths) != len(self.DEFAULT_WIDTHS) or any(w < 30 for w in widths):
            widths = list(self.DEFAULT_WIDTHS)
        header = self.tree.header()
        header.blockSignals(True)
        for i, w in enumerate(widths):
            header.resizeSection(i, w)
        header.blockSignals(False)

    def _apply_column_visibility(self):
        header = self.tree.header()
        hidden = set(self.cfg.hidden_columns)
        for key, index in self.COLUMN_INDEX.items():
            header.setSectionHidden(index, key in hidden)

    def set_column_visible(self, key: str, visible: bool):
        """Blendet eine Spalte der Hauptansicht ein oder aus (nur lokal gespeichert, der Name bleibt immer sichtbar)."""
        if key not in COLUMN_KEYS:
            return
        hidden = [k for k in COLUMN_KEYS if k in self.cfg.hidden_columns and k != key]
        if not visible:
            hidden.append(key)
        self.cfg.hidden_columns = [k for k in COLUMN_KEYS if k in hidden]
        self._apply_column_visibility()
        self._save_no_sync()  # reiner Anzeigezustand: kein Abgleich nötig

    def _header_menu(self, pos):
        """Rechtsklick auf die Spaltenköpfe: Spalten ein- und ausblenden."""
        menu = QMenu(self)
        labels = {"type": tr("Typ"), "target": tr("Ziel"), "terminal": tr("Terminal"), "tags": tr("Tags"), "os": tr("Betriebssystem")}
        for key in COLUMN_KEYS:
            action = menu.addAction(labels[key])
            action.setCheckable(True)
            action.setChecked(key not in self.cfg.hidden_columns)
            action.toggled.connect(lambda checked, k=key: self.set_column_visible(k, checked))
        menu.exec(self.tree.header().mapToGlobal(pos))

    def _flush_widths(self):
        header = self.tree.header()
        old = list(self.cfg.column_widths) if len(self.cfg.column_widths) == header.count() else list(self.DEFAULT_WIDTHS)
        # ausgeblendete Spalten behalten ihre Breite (Qt meldet für sie 0)
        widths = [old[i] if header.isSectionHidden(i) else header.sectionSize(i) for i in range(header.count())]
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

    # --- Integriertes Terminal ---
    def _terminal_tabs(self) -> list[TerminalTab]:
        return [w for i in range(1, self.tabs.count()) if isinstance(w := self.tabs.widget(i), TerminalTab)]

    def _terminal_views(self) -> list[TerminalView]:
        return [v for tab in self._terminal_tabs() for v in tab.views()]

    @staticmethod
    def _tab_of(view: TerminalView) -> TerminalTab | None:
        widget = view.parentWidget()
        while widget is not None and not isinstance(widget, TerminalTab):
            widget = widget.parentWidget()
        return widget

    def _refresh_tab(self, tab: TerminalTab):
        index = self.tabs.indexOf(tab)
        if index > 0:
            self.tabs.setTabText(index, tab.title())
            self.tabs.setTabToolTip(index, tab.title())

    def _open_embedded(self, args: list[str], title: str, env: dict[str, str]) -> TerminalView:
        """Wird vom Launcher gerufen, wenn das gewählte Terminal das integrierte ist: öffnet einen Reiter oder eine Kachel."""
        if not terminal_available():
            raise launcher.LaunchError(tr("Das Paket „pyte“ ist nicht installiert (pip install pyte)."))
        view = TerminalView(title, theme.scale(), theme.is_dark())
        view.closed.connect(lambda _code, v=view: self._terminal_closed(v))
        view.copied.connect(lambda text, v=view: self._terminal_copied(text, v.title))
        view.history_requested.connect(lambda v=view: self.show_clipboard_popup(v))
        view.split_requested.connect(lambda value, v=view: self._split_view(v, Qt.Orientation(value)))
        view.focus_requested.connect(lambda direction, v=view: self._move_focus(v, direction))
        view.zoom_requested.connect(lambda v=view: self._zoom_view(v))
        view.close_requested.connect(lambda v=view: self._close_view(v))
        view.typed.connect(lambda data, v=view: self._broadcast_typed(v, data))
        view.pasted.connect(lambda text, v=view: self._broadcast_pasted(v, text))
        view.grid_requested.connect(self._tile_all)
        view.local_requested.connect(lambda v=view: self._local_beside(v))
        view.untile_requested.connect(lambda v=view: self._untile(self._tab_of(v)))
        view.broadcast_requested.connect(lambda v=view: self._toggle_broadcast(self._tab_of(v)))
        view.tile_dropped.connect(lambda source, zone, v=view: self._drop_tile(source, v, zone))
        target, self._split_target = self._split_target, None
        tab = self._tab_of(target[0]) if target is not None else None
        if tab is not None and self.tabs.indexOf(tab) > 0:
            tab.add_view(view, target[0], target[1])
            self.tabs.setCurrentWidget(tab)
        else:
            tab = self._add_tab(view)
            self.tabs.setCurrentWidget(tab)
        self._refresh_tab(tab)
        view.start(args, env)
        view.setFocus()
        return view

    def open_local_terminal(self):
        """Öffnet eine Shell auf diesem Rechner im integrierten Terminal (neuer Reiter)."""
        try:
            self._open_embedded(localshell.command(), tr("Lokal"), runtime.clean_env())
        except launcher.LaunchError as exc:
            QMessageBox.critical(self, tr("Terminal konnte nicht geöffnet werden"), str(exc))

    def _local_beside(self, view: TerminalView):
        """Strg+Umschalt+T im Terminal: lokale Shell als Kachel rechts neben dieser Sitzung."""
        self._recent_terminal_tab = self._tab_of(view) or self._recent_terminal_tab
        tab = self._tab_of(view)
        self._split_target = (view, Qt.Orientation.Horizontal) if tab is not None else None
        try:
            self._open_embedded(localshell.command(), tr("Lokal"), runtime.clean_env())
        except launcher.LaunchError as exc:
            QMessageBox.critical(self, tr("Terminal konnte nicht geöffnet werden"), str(exc))
        finally:
            self._split_target = None

    def _add_tab(self, view: TerminalView) -> TerminalTab:
        """Neuer Terminal-Reiter für eine Sitzung (wird nicht in den Vordergrund geholt)."""
        tab = TerminalTab(view)
        tab.changed.connect(lambda t=tab: self._refresh_tab(t))
        index = self.tabs.addTab(tab, view.title)
        self.tabs.setTabToolTip(index, view.title)
        view.show()
        self._update_tile_button()
        return tab

    def _drop_tab(self, tab: TerminalTab):
        index = self.tabs.indexOf(tab)
        if index > 0:
            self.tabs.removeTab(index)
        if self._recent_terminal_tab is tab:
            self._recent_terminal_tab = None
        tab.deleteLater()
        self._update_tile_button()

    def _split_view(self, view: TerminalView, orientation: Qt.Orientation):
        """Öffnet neben/unter der Kachel eine weitere Sitzung mit demselben Befehl (dieselbe Verbindung)."""
        if view.command is None:
            return
        args, env = view.command
        self._split_target = (view, orientation)
        try:
            new = self._open_embedded(args, view.title, env)
        except launcher.LaunchError as exc:
            QMessageBox.critical(self, tr("Verbindung konnte nicht geöffnet werden"), str(exc))
            return
        finally:
            self._split_target = None
        launcher.track_embedded(args, new)
        self._update_open_markers()

    def _open_tiled(self, orientation: Qt.Orientation):
        """Verbindung neben/unter der zuletzt benutzten Kachel öffnen (ohne Kachel: wie sonst in einem neuen Reiter)."""
        tab = self._last_terminal_tab()
        self._split_target = (tab.active(), orientation) if tab is not None and tab.active() is not None else None
        try:
            self.connect_selected(new=True)
        finally:
            self._split_target = None

    def _last_terminal_tab(self) -> TerminalTab | None:
        tab = getattr(self, "_recent_terminal_tab", None)
        if tab is None:
            return None
        try:
            return tab if self.tabs.indexOf(tab) > 0 else None
        except RuntimeError:  # Reiter wurde inzwischen gelöscht
            self._recent_terminal_tab = None
            return None

    def _move_focus(self, view: TerminalView, direction: str):
        tab = self._tab_of(view)
        if tab is not None:
            tab.move_focus(view, direction)

    def _zoom_view(self, view: TerminalView):
        tab = self._tab_of(view)
        if tab is not None:
            tab.toggle_zoom(view)

    def _terminal_closed(self, view: TerminalView):
        """Der Prozess ist zu Ende: die Kachel verschwindet, mit der letzten auch der Reiter (wie ein geschlossenes Terminalfenster)."""
        tab = self._tab_of(view)
        if tab is not None:
            tab.remove_view(view)
            if tab.is_empty():
                self._drop_tab(tab)
            else:
                self._refresh_tab(tab)
        view.deleteLater()
        self._update_open_markers()

    # --- Kacheln: Raster, Auflösen, Eingabe an alle ---
    def _tile_all(self):
        """Alle Sitzungen aller Terminal-Reiter als Raster in einem Reiter zeigen (der aktuelle, sonst der erste)."""
        tabs = self._terminal_tabs()
        views = [v for t in tabs for v in t.views()]
        if len(views) < 2:
            self.toasts.notify(tr("Zum Kacheln braucht es mindestens zwei Sitzungen."), 4000)
            return
        current = self.tabs.currentWidget()
        target = current if isinstance(current, TerminalTab) else tabs[0]
        for tab in tabs:
            if tab is not target:
                for view in tab.views():
                    tab.detach(view)
                self._drop_tab(tab)
        target.arrange_grid(views)
        self.tabs.setCurrentWidget(target)
        self._refresh_tab(target)
        target.focus_active()

    def _drop_tile(self, source: TerminalView, target: TerminalView, zone: str):
        """Eine gezogene Kachel wurde auf ``target`` abgelegt: im selben Reiter umordnen/tauschen, sonst in dessen Reiter holen."""
        src_tab, dst_tab = self._tab_of(source), self._tab_of(target)
        if source is target or src_tab is None or dst_tab is None:
            return
        if src_tab is dst_tab:
            dst_tab.move_view(source, target, zone)
        else:  # aus einem anderen Reiter: an die Seite setzen (in der Mitte rechts daneben), der alte Reiter verschwindet, wenn er leer wird
            src_tab.detach(source)
            if src_tab.is_empty():
                self._drop_tab(src_tab)
            else:
                self._refresh_tab(src_tab)
            orientation = Qt.Orientation.Vertical if zone in ("up", "down") else Qt.Orientation.Horizontal
            dst_tab.add_view(source, target, orientation, before=zone in ("left", "up"))
            self.tabs.setCurrentWidget(dst_tab)
        self._refresh_tab(dst_tab)
        source.setFocus()
        self._update_tile_button()

    def _untile(self, tab: TerminalTab | None):
        """Jede Kachel des Reiters bekommt wieder einen eigenen Reiter (die erste bleibt im Reiter)."""
        if tab is None or len(tab.views()) < 2:
            return
        for view in tab.views()[1:]:
            tab.detach(view)
            self._refresh_tab(self._add_tab(view))
        self._refresh_tab(tab)
        tab.focus_active()

    def _toggle_broadcast(self, tab: TerminalTab | None):
        """Schaltet „Eingabe an alle Kacheln“ für den Reiter um (nur mit mindestens zwei Kacheln)."""
        if tab is None:
            return
        if len(tab.views()) < 2:
            self.toasts.notify(tr("„Eingabe an alle“ braucht mindestens zwei Kacheln im Reiter."), 4000)
            return
        tab.set_broadcast(not tab.broadcast())
        if tab.broadcast():
            self.toasts.notify(tr("Eingabe geht jetzt an alle Kacheln dieses Reiters (rot umrandet)."), 6000)
        tab.focus_active()

    def _broadcast_peers(self, view: TerminalView) -> list[TerminalView]:
        tab = self._tab_of(view)
        if tab is None or not tab.broadcast():
            return []
        return [v for v in tab.views() if v is not view and v.poll() is None]

    def _broadcast_typed(self, view: TerminalView, data: bytes):
        for peer in self._broadcast_peers(view):
            peer.send_input(data)

    def _broadcast_pasted(self, view: TerminalView, text: str):
        peers = self._broadcast_peers(view)
        if peers and confirm_paste(self, text, len(peers)):
            for peer in peers:
                peer.paste_text(text)

    def _add_tile_actions(self, menu: QMenu, tab: TerminalTab | None):
        grid = menu.addAction(tr("Alle Sitzungen kacheln"))
        grid.setEnabled(len(self._terminal_views()) >= 2)
        grid.triggered.connect(lambda: self._tile_all())
        count = len(tab.views()) if tab is not None else 0
        untile = menu.addAction(tr("In einzelne Reiter auflösen"))
        untile.setEnabled(count >= 2)
        untile.triggered.connect(lambda: self._untile(tab))
        broadcast = menu.addAction(tr("Eingabe an alle Kacheln senden"))
        broadcast.setCheckable(True)
        broadcast.setChecked(bool(tab is not None and tab.broadcast()))
        broadcast.setEnabled(count >= 2)
        broadcast.triggered.connect(lambda: self._toggle_broadcast(tab))

    def _build_tile_button(self):
        """Schaltfläche „Kacheln“ rechts neben den Reitern (nur sichtbar, solange es Terminal-Reiter gibt)."""
        button = QToolButton()
        button.setText(tr("Kacheln"))
        button.setToolTip(tr("Sitzungen kacheln, Eingabe an alle senden"))
        button.setAutoRaise(True)
        button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(button)
        menu.aboutToShow.connect(lambda: self._fill_tile_menu(menu))
        button.setMenu(menu)
        button.setVisible(False)
        self.tabs.setCornerWidget(button, Qt.Corner.TopRightCorner)
        self.tile_button = button

    def _fill_tile_menu(self, menu: QMenu):
        menu.clear()
        current = self.tabs.currentWidget()
        tab = current if isinstance(current, TerminalTab) else None
        view = tab.active() if tab is not None else None
        if view is not None:
            menu.addAction(tr("Rechts teilen")).triggered.connect(lambda: self._split_view(view, Qt.Orientation.Horizontal))
            menu.addAction(tr("Unten teilen")).triggered.connect(lambda: self._split_view(view, Qt.Orientation.Vertical))
            menu.addSeparator()
        self._add_tile_actions(menu, tab)

    def _update_tile_button(self):
        button = getattr(self, "tile_button", None)
        if button is not None:
            button.setVisible(bool(self._terminal_tabs()))

    def _confirm_end(self, running: list[TerminalView]) -> bool:
        if len(running) == 1:
            text = tr("Die Sitzung „{name}“ läuft noch. Beenden?", name=running[0].title)
        else:
            text = tr("Im Reiter laufen noch {n} Sitzungen. Beenden?", n=len(running))
        answer = QMessageBox.question(self, tr("Sitzung beenden"), text,
                                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        return answer == QMessageBox.StandardButton.Yes

    def _end_views(self, views: list[TerminalView]):
        running = [v for v in views if v.poll() is None]
        if running and not self._confirm_end(running):
            return
        for view in views:
            if view.poll() is None:
                view.terminate()  # die Kachel schließt, sobald der Prozess endet
            else:
                self._terminal_closed(view)

    def _close_terminal_tab(self, index: int):
        tab = self.tabs.widget(index)
        if index == 0 or not isinstance(tab, TerminalTab):
            return
        self._end_views(tab.views())

    def _close_view(self, view: TerminalView):
        self._end_views([view])

    # --- Zwischenablage-Verlauf ---
    def _init_clipboard(self):
        """Verlauf laden und die Zwischenablage beobachten – nur solange Sesam im Vordergrund ist."""
        self.clipboard = ClipboardHistory(Path(self.path).parent / "clipboard.json")
        self.clipboard.load()
        self._internal_copy = ""  # zuletzt im Terminal kopierter Text: die Systemmeldung dazu nicht noch einmal aufnehmen
        self._clip_save = QTimer(self)
        self._clip_save.setSingleShot(True)
        self._clip_save.setInterval(800)  # Änderungen bündeln, große Texte nicht bei jedem Tastendruck verschlüsseln
        self._clip_save.timeout.connect(self.clipboard.save)
        self.clipboard.listeners.append(self._clip_save.start)
        QGuiApplication.clipboard().dataChanged.connect(self._system_clipboard_changed)
        app = QGuiApplication.instance()
        if app is not None:
            app.applicationStateChanged.connect(self._app_state_changed)
        self.tabs.tabBar().setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tabs.tabBar().customContextMenuRequested.connect(self._tab_menu)
        if self.clipboard.notice and self.clipboard.locked:
            QTimer.singleShot(0, lambda: self.toasts.notify(self.clipboard.notice, 8000))

    def _system_clipboard_changed(self):
        if QGuiApplication.applicationState() == Qt.ApplicationState.ApplicationActive:  # was in anderen Programmen kopiert wird, bleibt unberührt
            self._read_system_clipboard()

    def _app_state_changed(self, state):
        if state == Qt.ApplicationState.ApplicationActive:  # Kopien aus anderen Programmen, die Sesam im Hintergrund nicht sehen durfte
            self._read_system_clipboard()

    PRIVATE_CLIPBOARD_FORMATS = ("x-kde-passwordManagerHint", "ExcludeClipboardContentFromMonitorProcessing", "org.nspasteboard.ConcealedType")

    def _read_system_clipboard(self):
        if not self.clipboard.settings.enabled:
            return
        mime = QGuiApplication.clipboard().mimeData()
        if mime is None or not mime.hasText() or any(mime.hasFormat(f) for f in self.PRIVATE_CLIPBOARD_FORMATS):
            return  # Passwortmanager markieren geheime Kopien
        text = mime.text()
        if text and text == self._internal_copy:
            return
        self._internal_copy = ""
        self._record_clip(text, "")

    def _terminal_copied(self, text: str, title: str):
        self._internal_copy = text
        self._record_clip(text, title)

    def _record_clip(self, text: str, source: str):
        if self.clipboard.too_large(text):
            self.toasts.notify(tr("Der kopierte Text ist zu groß für den Verlauf."), 4000)
            return
        self.clipboard.add(text, source)

    def _terminal_sessions(self) -> list[tuple[str, TerminalView]]:
        return [(v.title, v) for v in self._terminal_views() if v.poll() is None]

    def _paste_into(self, views: list, text: str, enter: bool = False):
        for view in views:
            view.paste_text(text, enter)

    def show_clipboard_popup(self, view: TerminalView):
        popup = ClipboardPopup(self.clipboard, self)
        popup.chosen.connect(lambda entry_id, enter, v=view: self._paste_entry(v, entry_id, enter))
        popup.move(view.mapToGlobal(view.rect().center()) - popup.rect().center())
        popup.show()
        popup.search.setFocus()
        self._clip_popup = popup

    def _paste_entry(self, view: TerminalView, entry_id: str, enter: bool):
        entry = self.clipboard.get(entry_id)
        if entry is None or self._tab_of(view) is None:
            return
        view.paste_text(entry.text, enter)
        view.setFocus()

    def open_clipboard(self):
        current = self.tabs.currentWidget()
        ClipboardDialog(self.clipboard, self._terminal_sessions, self._paste_into,
                        current.active() if isinstance(current, TerminalTab) else None, self).exec()

    def _tab_menu(self, pos):
        menu = QMenu(self)
        index = self.tabs.tabBar().tabAt(pos)
        tab = self.tabs.widget(index) if index > 0 else None
        if isinstance(tab, TerminalTab):
            view = tab.active()
            if view is not None:
                menu.addAction(tr("Rechts teilen")).triggered.connect(lambda: self._split_view(view, Qt.Orientation.Horizontal))
                menu.addAction(tr("Unten teilen")).triggered.connect(lambda: self._split_view(view, Qt.Orientation.Vertical))
            menu.addAction(tr("Sitzung beenden")).triggered.connect(lambda: self._close_terminal_tab(self.tabs.indexOf(tab)))
            menu.addSeparator()
        self._add_tile_actions(menu, tab if isinstance(tab, TerminalTab) else None)
        menu.addSeparator()
        sessions = self._terminal_sessions()
        everywhere = menu.addAction(tr("Zwischenablage in alle Reiter einfügen"))
        everywhere.setEnabled(bool(sessions) and bool(QGuiApplication.clipboard().text()))
        everywhere.triggered.connect(self._paste_clipboard_everywhere)
        menu.addAction(tr("Zwischenablage-Verlauf …")).triggered.connect(self.open_clipboard)
        menu.exec(self.tabs.tabBar().mapToGlobal(pos))

    def _paste_clipboard_everywhere(self):
        text = QGuiApplication.clipboard().text()
        views = [v for _, v in self._terminal_sessions()]
        if text and views and confirm_paste(self, text, len(views)):
            self._paste_into(views, text)

    def _tab_changed(self, index: int):
        widget = self.tabs.widget(index)
        if index > 0 and isinstance(widget, TerminalTab):
            self._recent_terminal_tab = widget
            widget.focus_active()
        elif index > 0 and widget is not None:
            widget.setFocus()
        else:
            self.tree.setFocus()

    def closeEvent(self, event):
        running = [v for v in self._terminal_views() if v.poll() is None]
        if running:
            answer = QMessageBox.question(
                self, tr("Beenden"), tr("Es laufen noch {n} Sitzungen im integrierten Terminal. Sesam beenden und die Sitzungen schließen?", n=len(running)),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        for view in running:
            view.terminate(force=True)
        launcher.set_embedded_opener(None)
        if self.clipboard.settings.clear_on_exit:
            self.clipboard.clear(include_pinned=False)
        self._clip_save.stop()  # nach dem Leeren: der Zeitgeber hat durch die Änderung neu gestartet
        self.clipboard.save()
        self.wg_indicator.stop()
        self._tunnel_timer.stop()
        self.remote.stop_all()  # die Tunnel des Fernzugriffs enden mit dem Programm
        self.tunnels.stop_all()  # Tunnel gehören zu dieser Sitzung und enden mit ihr
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
        if item.data(0, FAV_ROLE) == "head":
            self._fav_collapsed = collapsed
            return
        cid = item.data(0, ID_ROLE)
        if cid:
            (self._docker_expanded.discard if collapsed else self._docker_expanded.add)(cid)
            conn = self.cfg.connection_by_id(cid)
            if not collapsed and conn and _has_children(conn):
                # nicht mitten im Aufklappen am Baum ändern (Qt stürzt sonst ab)
                QTimer.singleShot(0, lambda: self._load_if_needed(cid))
            return
        path = item.data(0, GROUP_ROLE)
        if path is None or self.search.text().strip():
            return
        (self.collapsed.add if collapsed else self.collapsed.discard)(path)
        self._collapse_timer.start()

    def refresh(self, *_):
        self.tunnels.forget_missing(self.cfg)  # gelöschte oder geänderte Weiterleitungen laufen nicht weiter
        needle = self.search.text().strip().lower()
        wanted, text = _split_type_filter(needle)  # „typ:vnc“ zeigt nur VNC-Verbindungen (SSH, VNC, RDP)
        matches = [
            c
            for c in sorted(self.cfg.connections, key=lambda c: c.name.lower())
            if (not wanted or wanted <= {k.lower() for k in remote.kinds(c)}) and (not text or text in " ".join([c.name, c.host, c.user, c.group, *c.tags]).lower())
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
        favorites = [conn for conn in matches if conn.favorite] if self.cfg.favorites else []
        if favorites:  # fester Abschnitt ganz oben; die Verbindungen stehen zusätzlich in ihrer Gruppe
            head = QTreeWidgetItem([tr("Favoriten"), tr("{count} Verbindung(en)", count=len(favorites))])
            head.setData(0, FAV_ROLE, "head")
            head.setIcon(0, style.icon("star", GOLD, fill=GOLD))
            font = head.font(0)
            font.setBold(True)
            head.setFont(0, font)
            head.setForeground(1, muted)
            self.tree.invisibleRootItem().addChild(head)
            for conn in favorites:
                entry = self._connection_item(conn, c, muted, favorite_entry=True)
                head.addChild(entry)
            head.setExpanded(not self._fav_collapsed)
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
            item = self._connection_item(conn, c, muted)
            (nodes.get(conn.group) or self.tree.invisibleRootItem()).addChild(item)
            if _has_children(conn):
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

    def _connection_item(self, conn: Connection, c: dict[str, str], muted, favorite_entry: bool = False) -> QTreeWidgetItem:
        """Zeile einer Verbindung. ``favorite_entry``: Verweis im Favoriten-Abschnitt (ohne Stern, ohne Docker-/Proxmox-Unterelemente)."""
        kinds = remote.kinds(conn)
        item = QTreeWidgetItem([self._display_name(conn, favorite_entry), self._target(conn), self._terminal_name(conn), tag_util.format(conn.tags), conn.os_info, " · ".join(kinds)])
        item.setData(5, type_badges.KIND_ROLE, kinds)
        item.setToolTip(5, safe.tip(self._kind_tip(conn)))
        item.setData(0, ID_ROLE, conn.id)
        if favorite_entry:
            item.setData(0, FAV_ROLE, "entry")
        item.setIcon(0, self._server_icon(conn, launcher.is_open(conn.id)))
        item.setData(0, OPEN_ROLE, launcher.is_open(conn.id))
        for column in (2, 3, 4, 5):
            item.setForeground(column, muted)
        if conn.notes:
            item.setToolTip(0, safe.tip(conn.notes))
        return item

    @staticmethod
    def _kind_tip(conn: Connection) -> str:
        if not conn.remote:
            return tr("SSH-Verbindung")
        proto, port = remote.label(conn.remote), remote.port_of(conn)
        if conn.remote_mode == "direct":
            return tr("{proto} direkt zu Port {port}, ohne SSH (unverschlüsselt)", proto=proto, port=port)
        return tr("SSH-Verbindung; {proto} über einen SSH-Tunnel zu Port {port}", proto=proto, port=port)

    def _server_icon(self, conn: Connection, is_open: bool) -> QIcon:
        """Symbol der Verbindung (Server, bei reinem VNC/RDP-Ziel ein Bildschirm); mit grünem Punkt unten rechts, wenn sie offen ist."""
        direct = bool(conn.remote) and conn.remote_mode == "direct"
        icon = style.icon("monitor" if direct else "server", colors.hex_of(conn.color) or theme.colors()["muted"])
        if not is_open:
            return icon
        size = self.tree.iconSize()
        ratio = self.devicePixelRatioF()
        pixmap = icon.pixmap(size, ratio)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = pixmap.width(), pixmap.height()
        d = max(6, round(min(w, h) * 0.5))  # Durchmesser des Punkts
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(OPEN_COLOR))
        painter.drawEllipse(w - d - 1, h - d - 1, d, d)
        painter.end()
        return QIcon(pixmap)

    def _update_open_markers(self):
        """Setzt den grünen Punkt bei allen Verbindungszeilen neu, deren „offen“-Zustand sich geändert hat."""
        it = QTreeWidgetItemIterator(self.tree)
        while it.value():
            item = it.value()
            it += 1
            cid = item.data(0, ID_ROLE)
            if not cid or item.data(0, CONTAINER_ROLE):
                continue
            state = launcher.is_open(cid) or self.remote.is_open(cid)
            if bool(item.data(0, OPEN_ROLE)) == state:
                continue
            conn = self.cfg.connection_by_id(cid)
            if conn is not None:
                item.setIcon(0, self._server_icon(conn, state))
                item.setData(0, OPEN_ROLE, state)

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
        guest = self._selected_guest() if container else None
        port = self._selected_port() if container else None
        if port is not None:
            menu.addAction(self.a_connect)
            menu.addSeparator()
            menu.addAction(tr("Port einschalten") if port[1].disabled else tr("Port ausschalten")).triggered.connect(
                lambda: self.port_shutdown(not port[1].disabled))
            menu.addAction(tr("Beschreibung ändern …")).triggered.connect(self.port_description)
            menu.addAction(tr("Konfiguration speichern")).triggered.connect(lambda: self.save_device(port[0]))
            menu.addSeparator()
            menu.addAction(tr("Ports aktualisieren")).triggered.connect(lambda: self.reload_containers(port[0].id))
            menu.addSeparator()
        elif container:
            menu.addAction(self.a_connect)
            sub = _submenu(menu, tr("Verbinden mit …"))
            for t in self.cfg.terminals:
                a = sub.addAction(t.name)
                a.triggered.connect(lambda _=False, term=t: self.connect_container(term))
            if guest is None:
                menu.addAction(tr("Verbinden als …")).triggered.connect(lambda: self.connect_container(ask=True))
                box = self._selected_box()
                if box is not None:
                    menu.addSeparator()
                    labels = {"start": tr("Starten"), "stop": tr("Stoppen"), "restart": tr("Neustarten"), "remove": tr("Löschen …")}
                    for action in docker.ACTIONS:
                        if action == "remove":
                            menu.addSeparator()
                        a = menu.addAction(labels[action])
                        a.setEnabled(not box[1].running if action == "start" else box[1].running if action in ("stop", "restart") else True)
                        a.triggered.connect(lambda _=False, act=action: self.container_action(act))
            else:
                menu.addSeparator()
                labels = {"start": tr("Starten"), "shutdown": tr("Herunterfahren"), "stop": tr("Stoppen (hart)"), "reboot": tr("Neustarten")}
                for action in proxmox.ACTIONS:
                    a = menu.addAction(labels[action])
                    a.setEnabled(guest[1].running != (action == "start"))
                    a.triggered.connect(lambda _=False, act=action: self.power_guest(act))
            menu.addSeparator()
            menu.addAction(tr("Gäste aktualisieren") if guest is not None else tr("Container aktualisieren")).triggered.connect(lambda: self.reload_containers(container[0].id))
            menu.addSeparator()
        elif conn:
            menu.addAction(self.a_connect)
            menu.addAction(tr("Neue Sitzung")).triggered.connect(lambda: self.connect_selected(new=True))
            tile_right = menu.addAction(tr("Neben dem aktuellen Terminal öffnen"))
            tile_below = menu.addAction(tr("Unter dem aktuellen Terminal öffnen"))
            tile_right.setEnabled(self._last_terminal_tab() is not None)
            tile_below.setEnabled(self._last_terminal_tab() is not None)
            tile_right.triggered.connect(lambda: self._open_tiled(Qt.Orientation.Horizontal))
            tile_below.triggered.connect(lambda: self._open_tiled(Qt.Orientation.Vertical))
            menu.addAction(tr("Als root verbinden (sudo -i)")).triggered.connect(lambda: self.connect_selected(sudo=True))
            sub = _submenu(menu, tr("Verbinden mit …"))
            for t in self.cfg.terminals:
                a = sub.addAction(t.name)
                a.triggered.connect(lambda _=False, term=t: self.connect_selected(term))
            if conn.remote and conn.remote_mode == "direct":
                menu.addAction(tr("Per SSH verbinden")).triggered.connect(lambda: self._open_connection(conn))
            menu.addAction(self.a_files)
            self._remote_menu(menu, conn)
            if self.cfg.favorites:
                menu.addAction(tr("Aus Favoriten entfernen") if conn.favorite else tr("Zu Favoriten hinzufügen")).triggered.connect(
                    lambda: self.toggle_favorite([conn]))
            self._forwards_menu(menu, conn)
            menu.addAction(tr("Betriebssystem ermitteln")).triggered.connect(self.detect_os_selected)
            self._snippet_menu(menu, [conn])
            self._color_menu(menu, conn.color, lambda key: self.set_connection_color(conn, key))
            if conn.cisco:
                menu.addAction(tr("Konfiguration speichern")).triggered.connect(lambda: self.save_device(conn))
            if _has_children(conn):
                menu.addAction(tr("Ports aktualisieren") if conn.cisco else tr("Gäste aktualisieren") if conn.proxmox else tr("Container aktualisieren")).triggered.connect(lambda: self.reload_containers(conn.id))
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
        menu.addAction(self.a_dedupe)
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
        if self.cfg.favorites:
            all_fav = all(c.favorite for c in conns)
            menu.addAction(tr("Aus Favoriten entfernen") if all_fav else tr("Zu Favoriten hinzufügen")).triggered.connect(
                lambda: self.toggle_favorite(conns))
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
    def _find_items(self, conn_id: str) -> list[QTreeWidgetItem]:
        """Alle Zeilen der Verbindung: die in ihrer Gruppe und, bei Favoriten, der Verweis im Favoriten-Abschnitt."""
        found = []
        it = QTreeWidgetItemIterator(self.tree)
        while it.value():
            if it.value().data(0, ID_ROLE) == conn_id:
                found.append(it.value())
            it += 1
        return found

    def _find_item(self, conn_id: str) -> QTreeWidgetItem | None:
        """Die Zeile der Verbindung in ihrer Gruppe (der Verweis bei den Favoriten zählt nur, wenn es keine andere gibt)."""
        items = self._find_items(conn_id)
        return next((i for i in items if i.data(0, FAV_ROLE) != "entry"), items[0] if items else None)

    def _selected_container(self) -> tuple[Connection, str] | None:
        item = self.tree.currentItem()
        data = item.data(0, CONTAINER_ROLE) if item else None
        cid, _sep, name = data.partition("\x1f") if data else ("", "", "")
        conn = self.cfg.connection_by_id(cid) if cid else None
        return (conn, name) if conn else None

    def _selected_guest(self) -> tuple[Connection, proxmox.Guest] | None:
        target = self._selected_container()
        if not target or not target[0].proxmox:
            return None
        conn, vmid = target
        state = self._containers.get(conn.id)
        for guest in state if isinstance(state, list) else []:
            if str(guest.vmid) == vmid:
                return conn, guest
        return None

    def _selected_box(self) -> tuple[Connection, docker.Container] | None:
        """Der gewählte Docker-Container samt Host (nicht bei Proxmox-Gästen und Cisco-Ports)."""
        target = self._selected_container()
        if not target or target[0].proxmox or target[0].cisco:
            return None
        conn, name = target
        state = self._containers.get(conn.id)
        for box in state if isinstance(state, list) else []:
            if box.name == name:
                return conn, box
        return None

    def _selected_port(self) -> tuple[Connection, cisco.Port] | None:
        target = self._selected_container()
        if not target or not target[0].cisco:
            return None
        conn, name = target
        state = self._containers.get(conn.id)
        for port in state if isinstance(state, list) else []:
            if port.name == name:
                return conn, port
        return None

    def _info_item(self, text: str, tooltip: str = "") -> QTreeWidgetItem:
        item = QTreeWidgetItem([text])
        item.setForeground(0, QBrush(QColor(theme.colors()["muted"])))
        font = item.font(0)
        font.setItalic(True)
        item.setFont(0, font)
        if tooltip:
            item.setToolTip(0, safe.tip(tooltip))
        return item

    def _fill_containers(self, host_item: QTreeWidgetItem, conn: Connection):
        """Hängt die Unterelemente eines Docker-Hosts passend zum Ladezustand an (nur an frisch erzeugte Elemente)."""
        self._add_container_children(host_item, conn)

    def _add_container_children(self, host_item: QTreeWidgetItem, conn: Connection):
        c = theme.colors()
        icon = style.icon("box", c["accent"])
        muted = QBrush(QColor(c["muted"]))
        state = self._containers.get(conn.id, "")
        guests = conn.proxmox
        ports = conn.cisco
        if conn.id not in self._containers:
            host_item.addChild(self._info_item("…", tr("Aufklappen, um die Ports zu laden") if ports else tr("Aufklappen, um die Gäste zu laden") if guests else tr("Aufklappen, um die Container zu laden")))
        elif state is None:
            host_item.addChild(self._info_item(tr("Ports werden geladen …") if ports else tr("Gäste werden geladen …") if guests else tr("Container werden geladen …")))
        elif isinstance(state, str):
            host_item.addChild(self._info_item("⚠ " + state, state))
        elif not state:
            host_item.addChild(self._info_item(tr("Keine Ports") if ports else tr("Keine Gäste") if guests else tr("Keine Container")))
        elif ports:
            for port in state:
                label = tr("aus") if port.disabled else tr("verbunden") if port.up else port.status or "?"
                details = " · ".join(filter(None, [label, port.description, f"VLAN {port.vlan}" if port.vlan and port.vlan != "--" else "", port.speed]))
                child = QTreeWidgetItem([port.name, details, ""])
                child.setData(0, CONTAINER_ROLE, f"{conn.id}\x1f{port.name}")
                color = c["accent"] if port.up else c["muted"]
                child.setIcon(0, style.icon("server", color))
                child.setForeground(1, muted)
                if not port.up:
                    child.setForeground(0, muted)
                child.setToolTip(0, safe.tip("\n".join(filter(None, [port.name, port.description, port.status, port.type]))))
                child.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                host_item.addChild(child)
        elif guests:
            for guest in state:
                kind = tr("Container") if guest.is_container else tr("VM")
                status = tr("läuft") if guest.running else tr("gestoppt")
                child = QTreeWidgetItem([guest.title, f"{kind} · {status}", ""])
                child.setData(0, CONTAINER_ROLE, f"{conn.id}\x1f{guest.vmid}")
                child.setIcon(0, style.icon("box" if guest.is_container else "server", c["accent"] if guest.running else c["muted"]))
                child.setForeground(1, muted)
                if not guest.running:
                    child.setForeground(0, muted)
                child.setToolTip(0, safe.tip("\n".join(filter(None, [guest.title, f"{kind} · {status}", guest.node]))))
                child.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                host_item.addChild(child)
        else:
            for box in state:
                child = QTreeWidgetItem([box.name, f"{box.image} · {box.status}" if box.status else box.image, ""])
                child.setData(0, CONTAINER_ROLE, f"{conn.id}\x1f{box.name}")
                child.setIcon(0, icon if box.running else style.icon("box", c["muted"]))
                child.setForeground(1, muted)
                if not box.running:
                    child.setForeground(0, muted)
                child.setToolTip(0, safe.tip("\n".join(filter(None, [box.name, box.image, box.status, box.ports]))))
                child.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)  # nicht verschiebbar
                host_item.addChild(child)

    def _current_key(self) -> tuple | None:
        item = self.tree.currentItem()
        if item is None:
            return None
        if item.data(0, CONTAINER_ROLE):
            return ("container", item.data(0, CONTAINER_ROLE))
        if item.data(0, ID_ROLE):
            return ("conn", item.data(0, ID_ROLE), item.data(0, FAV_ROLE) == "entry")
        if item.data(0, GROUP_ROLE) is not None:
            return ("group", item.data(0, GROUP_ROLE))
        return None

    def _restore_key(self, key: tuple | None) -> bool:
        if key is None:
            return False
        role = {"container": CONTAINER_ROLE, "conn": ID_ROLE, "group": GROUP_ROLE}[key[0]]
        in_favorites = len(key) > 2 and key[2]
        fallback = None
        it = QTreeWidgetItemIterator(self.tree)
        while it.value():
            item = it.value()
            if item.data(0, role) == key[1]:
                if role != ID_ROLE or (item.data(0, FAV_ROLE) == "entry") == in_favorites:
                    self.tree.setCurrentItem(item)
                    return True
                fallback = fallback or item  # die Zeile in der anderen Rolle (z. B. nach dem Entfernen aus den Favoriten)
            it += 1
        if fallback is not None:
            self.tree.setCurrentItem(fallback)
            return True
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
                lister = (proxmox.list_guests if snapshot.proxmox else cisco.list_ports if snapshot.cisco else docker.list_containers)
                found = lister(self.cfg, snapshot, password=password)
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
        if conn is None or not _has_children(conn):
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
        if conn and _has_children(conn) and conn_id in self._docker_expanded and missing:
            self._load_containers(conn)

    def reload_containers(self, conn_id: str):
        conn = self.cfg.connection_by_id(conn_id)
        item = self._find_item(conn_id)
        if conn is None or item is None or not _has_children(conn):
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
        if conn.cisco:  # Ports haben keine eigene Verbindung: Doppelklick öffnet das Gerät
            try:
                launcher.launch(self.cfg, conn, terminal)
                self._touch(conn)
            except launcher.LaunchError as exc:
                QMessageBox.critical(self, tr("Verbindung konnte nicht geöffnet werden"), str(exc))
            return
        if conn.proxmox:
            self._connect_guest(terminal)
            return
        box = self._selected_box()
        if box is not None and not box[1].running:
            QMessageBox.information(self, tr("Verbindung konnte nicht geöffnet werden"),
                                    tr("Der Container „{name}“ läuft nicht.", name=name))
            return
        shell = user = None  # None = Vorgaben der Verbindung
        if ask:
            dlg = DockerConnectDialog(name, conn.docker_shell, conn.docker_user, self)
            if not dlg.exec():
                return
            shell, user = dlg.values()
        try:
            titled, command = docker.container_target(conn, name, shell, user)
            launcher.launch(self.cfg, titled, terminal, remote_command=command)
            self._touch(conn)
        except (docker.DockerError, launcher.LaunchError) as exc:
            QMessageBox.critical(self, tr("Verbindung konnte nicht geöffnet werden"), str(exc))

    def container_action(self, action: str):
        """Startet, stoppt, startet neu oder löscht den gewählten Docker-Container. Stoppen, Neustart und Löschen fragen vorher nach."""
        target = self._selected_box()
        if not target or action not in docker.ACTIONS:
            return
        conn, box = target
        questions = {
            "stop": tr("Container „{name}“ stoppen?", name=box.name),
            "restart": tr("Container „{name}“ neu starten?", name=box.name),
            "remove": (tr("Container „{name}“ läuft noch. Er wird beendet und gelöscht. Seine Volumes bleiben erhalten. Fortfahren?", name=box.name)
                       if box.running else
                       tr("Container „{name}“ löschen? Seine Volumes bleiben erhalten.", name=box.name)),
        }
        if action in questions and QMessageBox.question(self, tr("Bestätigen"), questions[action]) != QMessageBox.StandardButton.Yes:
            return
        snapshot, password = replace(conn), self._docker_passwords.get(conn.id)

        def work(job):
            try:
                docker.run_action(self.cfg, snapshot, box, action, password=password)
            except Exception as exc:
                return job, conn.id, exc
            return job, conn.id, None

        job = Job(work, self)
        self._docker_jobs.add(job)
        job.result.connect(self._container_action_done)
        job.start()

    @Slot(object)
    def _container_action_done(self, res):
        job, cid, exc = res
        self._docker_jobs.discard(job)
        if exc is not None:
            QMessageBox.critical(self, tr("Docker"), str(exc))
        conn = self.cfg.connection_by_id(cid)
        if conn is not None and conn.docker and not conn.proxmox and not conn.cisco:
            self.reload_containers(cid)  # auch nach einem Fehler: der Stand könnte sich trotzdem geändert haben

    def _connect_guest(self, terminal: Terminal | None = None):
        """LXC: ``pct enter`` über den Host. VM: Adresse per Gast-Agent erfragen, dann SSH vom Host aus in die VM."""
        target = self._selected_guest()
        if not target:
            return
        conn, guest = target
        try:
            if guest.is_container:
                titled, command = proxmox.guest_target(conn, guest, proxmox.shell_command(guest))
                launcher.launch(self.cfg, titled, terminal, remote_command=command)
                self._touch(conn)
                return
        except (proxmox.ProxmoxError, launcher.LaunchError) as exc:
            QMessageBox.critical(self, tr("Verbindung konnte nicht geöffnet werden"), str(exc))
            return
        if not guest.running:
            QMessageBox.information(self, tr("Verbindung konnte nicht geöffnet werden"),
                                    tr("Die VM „{name}“ läuft nicht.", name=guest.title))
            return
        snapshot, password = replace(conn), self._docker_passwords.get(conn.id)

        def work(job):
            try:
                return job, conn, guest, terminal, proxmox.vm_address(self.cfg, snapshot, guest, password=password), None
            except Exception as exc:
                return job, conn, guest, terminal, "", exc

        job = Job(work, self)
        self._docker_jobs.add(job)
        job.result.connect(self._vm_address_found)
        job.start()

    @Slot(object)
    def _vm_address_found(self, res):
        job, conn, guest, terminal, address, exc = res
        self._docker_jobs.discard(job)
        try:
            if exc is not None:
                raise exc
            titled, command = proxmox.guest_target(conn, guest, proxmox.vm_command(address, conn.proxmox_vm_user))
            launcher.launch(self.cfg, titled, terminal, remote_command=command)
            self._touch(conn)
        except (proxmox.ProxmoxError, sftp.SftpError, launcher.LaunchError) as exc:
            QMessageBox.critical(self, tr("Verbindung konnte nicht geöffnet werden"), str(exc))

    def power_guest(self, action: str):
        """Startet/stoppt den gewählten Gast. Herunterfahren und Neustart laufen sanft; Stoppen und Neustart fragen vorher nach."""
        target = self._selected_guest()
        if not target or action not in proxmox.ACTIONS:
            return
        conn, guest = target
        if action in ("stop", "reboot"):
            question = (tr("„{name}“ hart stoppen? Ungespeicherte Daten gehen verloren.", name=guest.title) if action == "stop"
                        else tr("„{name}“ neu starten?", name=guest.title))
            if QMessageBox.question(self, tr("Bestätigen"), question) != QMessageBox.StandardButton.Yes:
                return
        snapshot, password = replace(conn), self._docker_passwords.get(conn.id)

        def work(job):
            try:
                proxmox.power(self.cfg, snapshot, guest, action, password=password)
            except Exception as exc:
                return job, conn.id, exc
            return job, conn.id, None

        job = Job(work, self)
        self._docker_jobs.add(job)
        job.result.connect(self._power_done)
        job.start()

    def _device_job(self, conn: Connection, fn, done_text: str | None = None):
        """Führt ``fn(snapshot, password)`` am Cisco-Gerät im Hintergrund aus, meldet Fehler und lädt danach die Ports neu."""
        snapshot, password = replace(conn), self._docker_passwords.get(conn.id)

        def work(job):
            try:
                fn(snapshot, password)
            except Exception as exc:
                return job, conn.id, exc
            return job, conn.id, None

        job = Job(work, self)
        self._docker_jobs.add(job)
        job.result.connect(self._device_done)
        job.start()

    @Slot(object)
    def _device_done(self, res):
        job, cid, exc = res
        self._docker_jobs.discard(job)
        if exc is not None:
            QMessageBox.critical(self, tr("Cisco"), str(exc))
            return
        conn = self.cfg.connection_by_id(cid)
        if conn is not None and conn.cisco:
            self.reload_containers(cid)

    def port_shutdown(self, shutdown: bool):
        """Schaltet den gewählten Port aus (``shutdown``) bzw. ein (``no shutdown``); Ausschalten fragt vorher nach."""
        target = self._selected_port()
        if not target:
            return
        conn, port = target
        if shutdown and QMessageBox.question(
                self, tr("Bestätigen"), tr("Port „{name}“ ausschalten? Geräte an diesem Port verlieren die Verbindung.", name=port.name)
        ) != QMessageBox.StandardButton.Yes:
            return
        self._device_job(conn, lambda c, pw: cisco.set_shutdown(self.cfg, c, port.name, shutdown, password=pw))

    def port_description(self):
        target = self._selected_port()
        if not target:
            return
        conn, port = target
        text, ok = QInputDialog.getText(self, tr("Beschreibung"), tr("Beschreibung für {name}:", name=port.name),
                                        QLineEdit.EchoMode.Normal, port.description)
        if not ok:
            return
        try:
            cisco.check_description(text)
        except cisco.CiscoError as exc:
            QMessageBox.warning(self, tr("Beschreibung"), str(exc))
            return
        self._device_job(conn, lambda c, pw: cisco.set_description(self.cfg, c, port.name, text, password=pw))

    def save_device(self, conn: Connection):
        """Speichert die laufende Konfiguration des Geräts dauerhaft (write memory)."""
        self._device_job(conn, lambda c, pw: cisco.save_config(self.cfg, c, password=pw))

    @Slot(object)
    def _power_done(self, res):
        job, cid, exc = res
        self._docker_jobs.discard(job)
        if exc is not None:
            QMessageBox.critical(self, tr("Proxmox"), str(exc))
            return
        conn = self.cfg.connection_by_id(cid)
        if conn is not None and conn.proxmox:  # Proxmox schaltet im Hintergrund: nach kurzer Pause den neuen Status holen
            QTimer.singleShot(3000, lambda: self._reload_if_current(cid))

    def _reload_if_current(self, cid: str):
        conn = self.cfg.connection_by_id(cid)
        if conn is not None and conn.proxmox:
            self.reload_containers(cid)

    # --- Verbindungen ---
    def open_files(self):
        """Öffnet den Dateibrowser (SFTP) für die gewählte Verbindung."""
        conn = self._selected()
        if conn:
            self._open_files(conn)

    def _open_files(self, conn: Connection):
        window = SftpWindow(self.cfg, conn)
        self._sftp_windows.append(window)
        window.closed.connect(lambda w: self._sftp_windows.remove(w) if w in self._sftp_windows else None)
        window.show()
        self._touch(conn)

    # --- Favoriten, zuletzt verwendet, Schnellstarter ---
    def _touch(self, conn: Connection) -> None:
        """Merkt sich den Zeitpunkt der Nutzung (nur lokal, für den Schnellstarter)."""
        self.cfg.recent[conn.id] = time.time()
        if len(self.cfg.recent) > storage.RECENT_MAX:
            keep = sorted(self.cfg.recent.items(), key=lambda kv: kv[1], reverse=True)[:storage.RECENT_MAX]
            self.cfg.recent = dict(keep)
        self._save_no_sync()

    def toggle_favorite(self, conns: list[Connection]) -> None:
        """Alle als Favorit markieren; sind schon alle Favoriten, die Markierung wieder entfernen."""
        if not conns:
            return
        mark = not all(c.favorite for c in conns)
        for c in conns:
            c.favorite = mark
        key, bar = self._current_key(), self.tree.verticalScrollBar()
        scroll = bar.value()
        self._save()  # Favoriten werden mit synchronisiert
        self.refresh()
        self._restore_key(key)
        bar.setValue(scroll)

    def toggle_favorite_selected(self) -> None:
        self.toggle_favorite(self._selected_connections())

    def set_favorites(self, enabled: bool) -> None:
        """Favoriten in der Hauptansicht ein- oder ausblenden (wirkt sofort, nur lokal gespeichert; die Markierungen bleiben)."""
        self.cfg.favorites = bool(enabled)
        self.a_fav.setEnabled(self.cfg.favorites)
        key, bar = self._current_key(), self.tree.verticalScrollBar()
        scroll = bar.value()
        self._save_no_sync()
        self.refresh()
        self._restore_key(key)
        bar.setValue(scroll)

    def open_quick(self) -> None:
        """Schnellstarter (Strg+K): Verbindung suchen und öffnen."""
        def toggle(conn_id: str):
            conn = self.cfg.connection_by_id(conn_id)
            if conn is not None:
                self.toggle_favorite([conn])

        dlg = QuickLauncher(self.cfg, toggle, self)
        if not dlg.exec():
            return
        conn = self.cfg.connection_by_id(dlg.conn_id)
        if conn is None:
            return
        if dlg.action == "files":
            self._open_files(conn)
        else:
            self._open_connection(conn)

    # --- Port-Weiterleitungen (SSH-Tunnel) ---
    def _display_name(self, conn: Connection, favorite_entry: bool = False) -> str:
        """Name im Baum; Favoriten tragen dahinter ★ (nicht im Favoriten-Abschnitt selbst), laufende Tunnel ⇄ und die Anzahl."""
        text = conn.name + ("  ★" if conn.favorite and self.cfg.favorites and not favorite_entry else "")
        n = len(self.tunnels.active(conn.id))
        return f"{text}  ⇄ {n}" if n else text

    def _forwards_menu(self, menu: QMenu, conn: Connection) -> None:
        if not conn.forwards:
            return
        sub = _submenu(menu, tr("Weiterleitungen"))
        for f in conn.forwards:
            on = self.tunnels.state(conn.id, f.id) in (tunnels.STARTING, tunnels.RUNNING)
            a = sub.addAction(tunnels.label(f))
            a.setCheckable(True)
            a.setChecked(on)
            a.triggered.connect(lambda _=False, c=conn, fw=f: self.toggle_tunnel(c, fw))
        sub.addSeparator()
        sub.addAction(tr("Alle starten")).triggered.connect(lambda: [self.start_tunnel(conn, f) for f in conn.forwards])
        stop_all = sub.addAction(tr("Alle beenden"))
        stop_all.setEnabled(bool(self.tunnels.active(conn.id)))
        stop_all.triggered.connect(lambda: self.stop_tunnels(conn))

    def toggle_tunnel(self, conn: Connection, forward) -> None:
        if self.tunnels.state(conn.id, forward.id) in (tunnels.STARTING, tunnels.RUNNING):
            self.tunnels.stop(conn.id, forward.id)
            self._poll_tunnels()
        else:
            self.start_tunnel(conn, forward)

    def stop_tunnels(self, conn: Connection) -> None:
        self.tunnels.stop_connection(conn.id)
        self._poll_tunnels()

    def start_tunnel(self, conn: Connection, forward, ephemeral: bool = False) -> bool:
        """Startet die Weiterleitung. Unbekannte Host-Schlüssel werden vorher bestätigt (ein Tunnel hat kein Terminal für die Rückfrage)."""
        if self.tunnels.state(conn.id, forward.id) in (tunnels.STARTING, tunnels.RUNNING):
            return True
        try:
            eff, secret_id = launcher.resolve(self.cfg, conn)
            launcher.check_key(self.cfg, eff)
            password = passwords.get_password(secret_id) if eff.use_password else None
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                pending = tunnels.pending_host_keys(eff)
            finally:
                QApplication.restoreOverrideCursor()
            for key in pending:
                box = QMessageBox(self)
                box.setIcon(QMessageBox.Icon.Question)
                box.setWindowTitle(tr("Unbekannter Host-Schlüssel"))
                box.setTextFormat(Qt.TextFormat.PlainText)
                box.setText(tr("Der Host {host} ist noch nicht bekannt.", host=key.name))
                box.setInformativeText(tr("Fingerabdrücke:\n{fp}\n\nNur fortfahren, wenn ein Fingerabdruck stimmt.", fp=key.shown))
                yes = box.addButton(tr("Vertrauen und verbinden"), QMessageBox.ButtonRole.AcceptRole)
                box.addButton(QMessageBox.StandardButton.Cancel)
                box.setDefaultButton(QMessageBox.StandardButton.Cancel)
                box.exec()
                if box.clickedButton() is not yes:
                    return False
                tunnels.trust(key)
            self.tunnels.start(eff, forward, password, **({"ephemeral": True} if ephemeral else {}))
        except (launcher.LaunchError, passwords.PasswordError) as exc:
            QMessageBox.critical(self, tr("Tunnel konnte nicht gestartet werden"), str(exc))
            return False
        self._touch(conn)
        if not ephemeral:
            self.toasts.notify(tr("Tunnel wird gestartet: {label}", label=tunnels.label(forward)), 4000)
        self._poll_tunnels()
        return True

    # --- Fernzugriff (VNC und RDP) ---
    def _remote_menu(self, menu: QMenu, conn: Connection) -> None:
        if not conn.remote:
            return
        proto = remote.label(conn.remote)
        if self.remote.is_open(conn.id):
            menu.addAction(tr("{proto} beenden", proto=proto)).triggered.connect(lambda: self.close_remote(conn))
        else:
            menu.addAction(tr("{proto} öffnen", proto=proto)).triggered.connect(lambda: self.open_remote(conn))

    def open_remote(self, conn: Connection | None = None) -> None:
        conn = conn or self._selected()
        if conn is None:
            return
        if not conn.remote:
            self.toasts.notify(tr("Für „{name}“ ist der Fernzugriff nicht eingeschaltet (Verbindung bearbeiten → Fernzugriff).", name=conn.name or conn.host), 6000)
            return
        try:
            events = self.remote.begin(self.cfg, conn, lambda c, f: self.start_tunnel(c, f, ephemeral=True))
        except launcher.LaunchError as exc:
            QMessageBox.critical(self, tr("{proto} konnte nicht geöffnet werden", proto=remote.label(conn.remote)), str(exc))
            return
        self._touch(conn)
        self._show_remote_events(events)
        self._update_open_markers()

    def _open_builtin_viewer(self, conn: Connection, host: str, port: int, password: str | None, direct: bool):
        """Öffnet das Fenster des eingebauten VNC-Viewers (vom Fernzugriff-Manager aufgerufen)."""
        from .vnc_window import VncWindow

        window = VncWindow(remote.title(conn), host, port, password, direct)
        window.reconnect_requested.connect(lambda cid=conn.id: QTimer.singleShot(0, lambda: self._reconnect_remote(cid)))
        window.show()
        return window

    def _reconnect_remote(self, conn_id: str) -> None:
        """„Neu verbinden“ im eingebauten Viewer: Sitzung (und Tunnel) beenden und neu öffnen."""
        conn = self.cfg.connection_by_id(conn_id)
        if conn is not None:
            self.remote.stop(conn_id)
            self.open_remote(conn)

    def close_remote(self, conn: Connection) -> None:
        self.remote.stop(conn.id)
        self._update_open_markers()

    def _show_remote_events(self, events: list[tuple[str, bool]]) -> None:
        for text, error in events:
            self.toasts.notify(text, 0 if error else 4000, error=error)

    def _poll_remote(self) -> None:
        if self.remote.sessions:
            self._show_remote_events(self.remote.poll(self.cfg))
            self._update_open_markers()

    def _poll_tunnels(self) -> None:
        """Meldet beendete und aktive Tunnel und aktualisiert die Anzeige im Baum (läuft jede Sekunde)."""
        self._poll_remote()
        for t in self.tunnels.newly_failed():
            conn = self.cfg.connection_by_id(t.conn_id)
            text = tr("Tunnel „{name}“ wurde beendet: {error}", name=tunnels.label(t.forward), error=t.error)
            if conn is not None and not conn.use_password and conn.identity_id == "" and "denied" in t.error.lower():
                text += " " + tr("Ohne gespeichertes Passwort funktionieren Tunnel nur mit Schlüsseln ohne Passphrase.")
            self.toasts.notify(text, error=True)
        active = {t.key: t for t in self.tunnels.active()}
        for key, t in active.items():
            if t.state == tunnels.RUNNING and key not in self._tunnel_up:
                self._tunnel_up.add(key)
                self.toasts.notify(tr("Tunnel aktiv: {label}", label=tunnels.label(t.forward)), 4000)
        self._tunnel_up &= set(active)
        sig = tuple(sorted((k[0], k[1], t.state) for k, t in active.items()))
        if sig != self._tunnel_sig:
            changed = {k[0] for k in self._tunnel_sig} | {k[0] for k in sig}
            self._tunnel_sig = sig
            for cid in changed:
                conn = self.cfg.connection_by_id(cid)
                for item in self._find_items(cid):
                    if conn is not None:
                        item.setText(0, self._display_name(conn, item.data(0, FAV_ROLE) == "entry"))

    def connect_selected(self, terminal: Terminal | None = None, sudo: bool = False, new: bool = False):
        """Doppelklick/Enter: ist die Verbindung schon offen, gibt es nur einen Hinweis. ``new``: immer eine weitere Sitzung."""
        conn = self._selected()
        if not conn:
            if self._selected_container():
                self.connect_container(terminal)
            return
        terminal = terminal if isinstance(terminal, Terminal) else None
        if conn.remote and conn.remote_mode == "direct" and not (new or sudo or terminal):  # Gerät, das nur VNC/RDP spricht: Doppelklick öffnet den Fernzugriff
            self.open_remote(conn)
            return
        if not (new or sudo or terminal) and launcher.is_open(conn.id):
            self.toasts.notify(tr("„{name}“ ist bereits geöffnet. Eine weitere Sitzung: Rechtsklick → „Neue Sitzung“.", name=conn.name or conn.host), 6000)
            return
        self._open_connection(conn, terminal, sudo)

    def _open_connection(self, conn: Connection, terminal: Terminal | None = None, sudo: bool = False):
        if sudo:  # einmalig, wird nicht gespeichert
            conn = replace(conn, sudo_mode=conn.sudo_mode or "login")
        try:
            launcher.launch(self.cfg, conn, terminal)
        except launcher.LaunchError as exc:
            QMessageBox.critical(self, tr("Verbindung konnte nicht geöffnet werden"), str(exc))
            return
        self._touch(conn)
        self._update_open_markers()
        self._detect_os(conn)  # beim ersten Verbinden das Betriebssystem merken

    # --- Betriebssystem des Hosts ---
    OS_REFRESH_SECONDS = 7 * 24 * 3600  # bekanntes Betriebssystem beim Verbinden erneuern, wenn die Abfrage älter ist
    OS_RETRY_MS = (20000, 60000)  # unbekannter Host: erst nach der Bestätigung im Terminal ist sein Schlüssel eingetragen

    def _detect_os(self, conn: Connection, force: bool = False, attempt: int = 0):
        """Fragt das Betriebssystem im Hintergrund ab, wenn es noch nicht bekannt ist (``force``: auf Wunsch, mit Rückfragen)."""
        conn = self.cfg.connection_by_id(conn.id) or conn
        if conn.id in self._os_busy or not conn.host.strip():
            return
        if not force and (conn.id in self._os_tried or conn.jump_host.strip()):
            return
        if not force and conn.os_info and time.time() - conn.os_checked < self.OS_REFRESH_SECONDS:
            return
        self._os_busy.add(conn.id)
        snapshot = replace(conn)
        password = self._os_passwords.get(conn.id)

        def work(job):
            try:
                found = osinfo.detect(self.cfg, snapshot, password=password, strict=not force)
            except Exception as exc:  # jede Meldung wird unten ausgewertet
                return job, conn.id, force, attempt, "", exc
            return job, conn.id, force, attempt, found, None

        job = Job(work, self)
        self._os_jobs.add(job)
        job.result.connect(self._os_detected)
        job.start()

    @Slot(object)
    def _os_detected(self, res):
        job, cid, force, attempt, found, exc = res
        self._os_jobs.discard(job)
        self._os_busy.discard(cid)
        conn = self.cfg.connection_by_id(cid)
        if conn is None:
            return
        if exc is None and found:
            changed = found != conn.os_info
            conn.os_info, conn.os_checked = found, time.time()
            self._save()  # auch bei gleichem Wert: der Zeitpunkt der Abfrage steuert die nächste Auffrischung
            if changed:
                item = self._find_item(cid)
                if item is not None:
                    item.setText(self.COLUMN_INDEX["os"], found)
            return
        if isinstance(exc, sftp.SftpUnknownHostKey) and attempt < len(self.OS_RETRY_MS):
            QTimer.singleShot(self.OS_RETRY_MS[attempt], lambda: self._detect_os(conn, attempt=attempt + 1))
            return
        if force and isinstance(exc, sftp.SftpAuthError) and self._os_passwords.pop(cid, None) is None:
            text, ok = QInputDialog.getText(
                self, tr("Anmeldung"), tr("Passwort für {v}{host}:", v=conn.user + '@' if conn.user else '', host=conn.host),
                QLineEdit.EchoMode.Password)
            if ok and text:
                self._os_passwords[cid] = text
                self._detect_os(conn, force=True)
                return
        self._os_tried.add(cid)
        if force:
            self.toasts.notify(
                tr("Betriebssystem von {name} nicht ermittelt: {reason}", name=conn.name,
                   reason=str(exc) if exc is not None else tr("keine verwertbare Antwort")), 10000, error=True)

    def detect_os_selected(self):
        """Kontextmenü: Betriebssystem der gewählten Verbindung (erneut) ermitteln."""
        conn = self._selected()
        if conn is not None:
            self._os_tried.discard(conn.id)
            self._detect_os(conn, force=True)

    def _apply_password(self, conn: Connection, change: str | None) -> None:
        if change is None:
            return
        try:
            passwords.apply_change(conn, change)
        except passwords.PasswordError as exc:
            QMessageBox.critical(self, tr("Passwort"), str(exc))

    def _apply_remote_password(self, conn: Connection, change: str | None, before: tuple[str, bool] | None = None) -> None:
        """Wendet die Passwortänderung des Fernzugriffs an. ``before`` = (Protokoll, Passwort gespeichert) vor dem Bearbeiten:
        wechselt das Protokoll, wird das Passwort des alten gelöscht (es gehört zum alten Protokoll)."""
        try:
            if before and before[0] and before[1] and conn.remote != before[0]:
                try:
                    passwords.delete_password(remote.password_key(conn.id, before[0]))
                finally:
                    conn.remote_use_password = False
            remote.apply_password_change(conn, change)
        except passwords.PasswordError as exc:
            QMessageBox.critical(self, tr("Passwort"), str(exc))

    def new_connection(self):
        dlg = ConnectionDialog(self.cfg, parent=self, default_group=self._current_group_context(), save_callback=self._save)
        if dlg.exec():
            conn = dlg.result_connection()
            self._apply_password(conn, dlg.password_change)
            self._apply_remote_password(conn, dlg.remote_password_change)
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
            self._apply_remote_password(conn, dlg.remote_password_change, dlg.remote_before)
            self._docker_forget(conn.id)  # Zugang oder Docker-Einstellungen können sich geändert haben
            self._save()
            self.refresh()

    def duplicate_selected(self):
        conn = self._selected()
        if not conn:
            return
        clone = copy.deepcopy(conn)
        clone.id = new_id()
        clone.favorite = False  # die Kopie ist kein Favorit
        for f in clone.forwards:
            f.id = new_id()  # die Kopie hat eigene Tunnel
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
        if conn.remote_use_password and conn.remote:
            try:
                pw = passwords.get_password(remote.password_key(conn.id, conn.remote))
                if pw is not None:
                    passwords.set_password(remote.password_key(clone.id, conn.remote), pw)
                else:
                    clone.remote_use_password = False
            except passwords.PasswordError as exc:
                clone.remote_use_password = False
                QMessageBox.warning(self, tr("Passwort nicht kopiert"), str(exc))
        # Die Kopie erst nach dem Bestätigen des Dialogs anlegen; bei „Abbrechen“ entsteht nichts.
        dlg = ConnectionDialog(self.cfg, clone, parent=self, save_callback=self._save)
        dlg.name.selectAll()  # der Name lässt sich sofort überschreiben
        if dlg.exec():
            self._apply_password(clone, dlg.password_change)
            self._apply_remote_password(clone, dlg.remote_password_change, dlg.remote_before)
            self.cfg.connections.append(clone)
            self._save()
            self.refresh()
            self._select_connection(clone.id)
        else:
            for secret in (clone.id, remote.password_key(clone.id, conn.remote)):
                try:
                    passwords.delete_password(secret)  # die vorab kopierten Passwörter wieder entfernen
                except passwords.PasswordError:
                    pass

    def _select_connection(self, conn_id: str, add: bool = False) -> bool:
        """Wählt die Verbindung im Baum aus (klappt übergeordnete Gruppen auf). Liefert False, wenn sie nicht sichtbar ist."""
        item = self._find_item(conn_id)
        if item is None:
            return False
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
            self.remote.stop(conn.id)
            if conn.remote_use_password:
                try:
                    remote.forget_passwords(conn.id)
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

    def merge_duplicates(self):
        """Findet gleiche Verbindungen (Name, Host, Port, Benutzer, Gruppe) mit verschiedenen IDs und lässt je eine übrig."""
        groups = duplicates.find_duplicate_connections(self.cfg)
        if not groups:
            QMessageBox.information(self, tr("Doppelte Verbindungen"), tr("Keine doppelten Verbindungen gefunden."))
            return
        lines = "\n".join(f"• {g[0].name} ({launcher.destination(g[0])}) – {len(g)}×" for g in groups[:10])
        more = tr("\n… und {v} weitere", v=len(groups) - 10) if len(groups) > 10 else ""
        text = tr("{n} Verbindung(en) sind mehrfach vorhanden:\n\n{lines}{more}\n\nJeweils eine bleibt erhalten (bevorzugt die mit gespeichertem Passwort), die übrigen werden gelöscht. Fortfahren?",
                  n=len(groups), lines=lines, more=more)
        if QMessageBox.question(self, tr("Doppelte Verbindungen"), text) != QMessageBox.StandardButton.Yes:
            return
        removed, failures = duplicates.remove_duplicates(self.cfg, groups, passwords.delete_password)
        for conn in removed:
            self._docker_forget(conn.id)
            self._docker_expanded.discard(conn.id)
        self._save()
        self.refresh()
        if failures:
            QMessageBox.warning(self, tr("Passwort nicht gelöscht"), "\n".join(failures))

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
    def edit_terminals(self, parent=None):
        dlg = TerminalsDialog(self.cfg, parent or self)
        if dlg.exec():
            self.cfg.terminals, self.cfg.default_terminal_id = dlg.result_values()
            valid = {t.id for t in self.cfg.terminals}
            for c in self.cfg.connections:
                if c.terminal_id and c.terminal_id not in valid:
                    c.terminal_id = ""
            self._save()
            self.refresh()

    def edit_viewers(self, parent=None):
        dlg = ViewersDialog(self.cfg, parent or self)
        if dlg.exec():
            self.cfg.viewers, self.cfg.default_viewer_id, self.cfg.default_rdp_viewer_id = dlg.result_values()
            valid = {v.id for v in self.cfg.viewers}
            for c in self.cfg.connections:
                if c.remote_viewer_id and c.remote_viewer_id not in valid:
                    c.remote_viewer_id = ""
            self._save()

    # --- Synchronisation ---
    def _sync_status(self, text: str, error: bool):
        self.sync_label.setText(("⚠ " if error else "⟳ " if text.endswith("…") else "✓ ") + text)
        self.sync_label.setToolTip(safe.tip(text))
        if self.cfg.toasts:  # die Einblendung ersetzt das Etikett in der Statuszeile
            self.toasts.notify(text, error=error)

    def _sync_changed_config(self):
        try:
            storage.save(self.cfg, self.path)  # ohne erneuten Abgleich auszulösen
        except OSError:
            pass
        self.refresh()

    def _sync_finished(self, report, error, manual: bool):
        if error and manual:
            QMessageBox.warning(self, tr("Synchronisation fehlgeschlagen"), str(error))

    def _ask_sync_conflicts(self, conflicts):
        dlg = SyncConflictDialog(conflicts, self)
        return dlg.choices() if dlg.exec() else None

    def edit_sync(self, parent=None):
        dlg = SyncDialog(self.cfg, parent or self)
        if dlg.exec():
            self._save_no_sync()
            if self.sync.enabled():
                self.sync.sync(manual=True)

    def set_font_scale(self, percent: int):
        percent = theme.clamp_scale(percent)
        app = QApplication.instance()
        if app is not None:
            percent = theme.apply_scale(app, percent)
        for view in self._terminal_views():
            view.set_scale(percent)
        if percent != self.cfg.font_scale:
            self.cfg.font_scale = percent
            self._save_no_sync()  # reiner Anzeigezustand: kein Abgleich nötig

    def set_language(self, code: str):
        """Merkt die Sprache; sie wird beim nächsten Start angewandt (die Fenster sind bereits aufgebaut)."""
        self.cfg.language = i18n.normalize(code)
        self._save_no_sync()

    @staticmethod
    def _stored_elevation() -> wireguard.Elevation | None:
        """Das im Schlüsselbund hinterlegte sudo-Passwort; ohne Passwort, ohne sudo oder ohne Schlüsselbund ``None``."""
        if not wireguard.sudo_available():
            return None
        try:
            password = passwords.get_password(wireguard.SUDO_KEY)
        except passwords.PasswordError:
            return None
        return wireguard.Elevation(password) if password else None

    def _ask_sudo_password(self) -> wireguard.Elevation | None:
        """Fragt beim Einschalten das sudo-Passwort. Leer, abgebrochen oder falsch: ``None`` (einfache Anzeige)."""
        password, ok = QInputDialog.getText(
            self, tr("WireGuard"),
            tr("sudo-Passwort für Endpunkt und Handshake (optional).\nOhne Passwort zeigt Sesam die einfache Anzeige."),
            QLineEdit.EchoMode.Password)
        if not ok or not password:
            self.toasts.notify(tr("WireGuard: einfache Anzeige (kein sudo-Passwort)"), 6000)
            return None
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            valid = wireguard.verify_sudo_password(password)
        finally:
            QApplication.restoreOverrideCursor()
        if not valid:
            self.toasts.notify(tr("WireGuard: sudo-Passwort nicht akzeptiert, einfache Anzeige"), 8000, error=True)
            return None
        try:
            passwords.set_password(wireguard.SUDO_KEY, password)
        except passwords.PasswordError:
            pass  # ohne Schlüsselbund gilt das Passwort nur bis zum Beenden
        return wireguard.Elevation(password)

    def set_wireguard(self, enabled: bool):
        """Schaltet die WireGuard-Anzeige ein oder aus (wirkt sofort, nur lokal gespeichert).

        Beim Einschalten unter Linux wird das sudo-Passwort gefragt (siehe :meth:`_ask_sudo_password`); beim Ausschalten
        wird es wieder aus dem Schlüsselbund entfernt.
        """
        enabled = bool(enabled)
        if enabled and not self.cfg.wireguard and wireguard.sudo_available() and wireguard._wg_executable():
            self.wg_indicator.set_elevation(self._ask_sudo_password())  # ohne „wg“ gäbe es nichts zu entsperren
        elif not enabled:
            self.wg_indicator.set_elevation(None)
            try:
                passwords.delete_password(wireguard.SUDO_KEY)
            except passwords.PasswordError:
                pass
        self.cfg.wireguard = enabled
        self._save_no_sync()
        self.wg_indicator.set_enabled(enabled)

    def set_toasts(self, enabled: bool):
        """Meldungen als Einblendung (an) oder in der Statuszeile (aus); wirkt sofort, nur lokal gespeichert."""
        self.cfg.toasts = bool(enabled)
        self.toasts.clear()
        self.sync_label.setVisible(not self.cfg.toasts)
        self._save_no_sync()

    def edit_settings(self):
        dlg = SettingsDialog(self.cfg.font_scale, self.cfg.language, self.set_font_scale, self.set_language, self,
                             wireguard=self.cfg.wireguard, set_wireguard=self.set_wireguard,
                             toasts=self.cfg.toasts, set_toasts=self.set_toasts,
                             favorites=self.cfg.favorites, set_favorites=self.set_favorites,
                             open_terminals=self.edit_terminals, open_viewers=self.edit_viewers, open_sync=self.edit_sync)
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
        self._update_open_markers()
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
                action.setToolTip(safe.tip(sn.command))
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
