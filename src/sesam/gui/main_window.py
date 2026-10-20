from __future__ import annotations

import copy
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QHeaderView, QInputDialog, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox,
    QStyle, QToolBar, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from .. import groups as grp
from . import theme
from .. import launcher, passwords, storage
from ..models import Config, Connection, Terminal, new_id
from ..ssh_config import parse_ssh_config
from .connection_dialog import ConnectionDialog
from .identities_dialog import IdentitiesDialog
from .sync_controller import SyncController
from .sync_dialog import SyncDialog
from .terminals_dialog import TerminalsDialog

ID_ROLE = int(Qt.ItemDataRole.UserRole)  # Verbindungs-ID
GROUP_ROLE = ID_ROLE + 1  # Gruppenpfad


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
            target_group = parent.data(0, GROUP_ROLE) if parent else ""
        if source.data(0, GROUP_ROLE) is not None:
            self.moved.emit("group", source.data(0, GROUP_ROLE), target_group)
        elif source.data(0, ID_ROLE):
            self.moved.emit("conn", source.data(0, ID_ROLE), target_group)


class MainWindow(QMainWindow):
    def __init__(self, cfg: Config, path: Path):
        super().__init__()
        self.cfg = cfg
        self.path = path
        self.collapsed: set[str] = set()
        self.setWindowTitle("Sesam")
        self.resize(940, 580)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Suchen (Name, Host, Benutzer, Gruppe) …")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.refresh)

        self.tree = ConnectionTree()
        self.tree.setHeaderLabels(["Name", "Ziel", "Terminal"])
        self.tree.setAlternatingRowColors(True)
        header = self.tree.header()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)  # Breite frei ziehbar
        header.setStretchLastSection(True)
        header.setMinimumSectionSize(60)
        self._apply_column_widths()
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
        layout = QVBoxLayout(central)
        layout.addWidget(self.search)
        layout.addWidget(self.tree)
        self.setCentralWidget(central)

        self.sync = SyncController(self.cfg, lambda: storage.save(self.cfg, self.path), self)
        self.sync.status.connect(self._sync_status)
        self.sync.cfg_changed.connect(self._sync_changed_config)
        self.sync.finished.connect(self._sync_finished)
        self.sync_label = QLabel("")
        self.statusBar().addPermanentWidget(self.sync_label)

        self._build_actions()
        self.refresh()
        if self.sync.enabled() and self.cfg.sync.auto:
            QTimer.singleShot(500, lambda: self.sync.sync())  # beim Start abgleichen

    # --- Aufbau ---
    def _build_actions(self):
        def act(text, slot, shortcut=None):
            a = QAction(text, self)
            a.triggered.connect(lambda *_: slot())
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
            return a

        self.a_connect = act("Verbinden", self.connect_selected, "Return")
        self.a_new = act("Neue Verbindung", self.new_connection, "Ctrl+N")
        self.a_new_group = act("Neue Gruppe …", self.new_group, "Ctrl+Shift+N")
        self.a_edit = act("Bearbeiten", self.edit_selected, "F2")
        self.a_dup = act("Duplizieren", self.duplicate_selected, "Ctrl+D")
        self.a_del = act("Löschen", self.delete_selected, "Del")
        self.a_terms = act("Terminals …", self.edit_terminals)
        self.a_identities = act("Identitäten …", self.edit_identities)
        self.a_sync = act("Jetzt synchronisieren", lambda: self.sync.sync(manual=True), "F5")
        self.a_sync_settings = act("Synchronisation …", self.edit_sync)
        self.a_import = act("Aus ~/.ssh/config importieren", self.import_ssh_config)

        bar = QToolBar("Hauptleiste")
        bar.setMovable(False)
        for a in (self.a_connect, self.a_new, self.a_new_group, self.a_edit, self.a_dup, self.a_del):
            bar.addAction(a)
        bar.addSeparator()
        bar.addAction(self.a_sync)
        bar.addAction(self.a_identities)
        bar.addAction(self.a_terms)
        self.addToolBar(bar)

        menu = self.menuBar().addMenu("&Datei")
        menu.addAction(self.a_sync)
        menu.addAction(self.a_import)
        menu.addSeparator()
        menu.addAction(act("Beenden", self.close, "Ctrl+Q"))
        menu = self.menuBar().addMenu("&Einstellungen")
        menu.addAction(self.a_identities)
        menu.addAction(self.a_terms)
        menu.addAction(self.a_sync_settings)
        menu.addSeparator()
        theme_menu = menu.addMenu("Darstellung")
        self.theme_actions: dict[str, QAction] = {}
        group = QActionGroup(self)
        group.setExclusive(True)
        for mode in theme.MODES:
            a = QAction(theme.LABELS[mode], self)
            a.setCheckable(True)
            a.setChecked(self.cfg.theme == mode)
            a.triggered.connect(lambda _=False, m=mode: self.set_theme(m))
            group.addAction(a)
            theme_menu.addAction(a)
            self.theme_actions[mode] = a

    # --- Anzeige ---
    def _terminal_name(self, conn: Connection) -> str:
        t = self.cfg.terminal_by_id(conn.terminal_id) if conn.terminal_id else None
        return t.name if t else "Standard"

    def _target(self, conn: Connection) -> str:
        conn, _ = launcher.resolve(self.cfg, conn)
        base = f"{conn.user}@{conn.host}" if conn.user else conn.host
        return f"{base}:{conn.port}" if conn.port != 22 else base

    DEFAULT_WIDTHS = (280, 260, 160)

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
            self._save()

    def closeEvent(self, event):
        if self._width_timer.isActive():
            self._width_timer.stop()
            self._flush_widths()
        super().closeEvent(event)

    def _expansion_changed(self, item: QTreeWidgetItem, collapsed: bool):
        path = item.data(0, GROUP_ROLE)
        if path is None or self.search.text().strip():
            return
        (self.collapsed.add if collapsed else self.collapsed.discard)(path)

    def refresh(self, *_):
        needle = self.search.text().strip().lower()
        matches = [
            c
            for c in sorted(self.cfg.connections, key=lambda c: c.name.lower())
            if not needle or needle in " ".join([c.name, c.host, c.user, c.group]).lower()
        ]
        if needle:
            visible: set[str] = set()
            for c in matches:
                visible.update(grp.ancestors(c.group))
            group_paths = sorted(visible, key=str.lower)
        else:
            group_paths = grp.all_groups(self.cfg)

        icon = self.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon)
        self.tree.blockSignals(True)
        self.tree.clear()
        nodes: dict[str, QTreeWidgetItem] = {}
        for path in group_paths:  # sortiert: Eltern stehen vor Kindern
            item = QTreeWidgetItem([grp.name_of(path)])
            item.setData(0, GROUP_ROLE, path)
            item.setIcon(0, icon)
            font = item.font(0)
            font.setBold(True)
            item.setFont(0, font)
            parent = nodes.get(grp.parent_of(path))
            (parent or self.tree.invisibleRootItem()).addChild(item)
            nodes[path] = item
        for conn in matches:
            item = QTreeWidgetItem([conn.name, self._target(conn), self._terminal_name(conn)])
            item.setData(0, ID_ROLE, conn.id)
            if conn.notes:
                item.setToolTip(0, conn.notes)
            (nodes.get(conn.group) or self.tree.invisibleRootItem()).addChild(item)
        for path, item in nodes.items():
            item.setExpanded(bool(needle) or path not in self.collapsed)
            count = len(grp.connections_in(self.cfg, path))
            item.setText(1, f"{count} Verbindung(en)" if count else "leer")
        self.tree.blockSignals(False)

    def _selected(self) -> Connection | None:
        item = self.tree.currentItem()
        cid = item.data(0, ID_ROLE) if item else None
        return self.cfg.connection_by_id(cid) if cid else None

    def _selected_group(self) -> str | None:
        item = self.tree.currentItem()
        return item.data(0, GROUP_ROLE) if item else None

    def _current_group_context(self) -> str:
        """Gruppe, in der neue Elemente standardmäßig landen (aktuelle Auswahl)."""
        group = self._selected_group()
        if group is not None:
            return group
        conn = self._selected()
        return conn.group if conn else ""

    def _save(self):
        try:
            storage.save(self.cfg, self.path)
        except OSError as exc:
            QMessageBox.critical(self, "Speichern fehlgeschlagen", str(exc))
            return
        self.sync.schedule()  # Automatikmodus: verzögert abgleichen

    def _context_menu(self, pos):
        item = self.tree.itemAt(pos)
        if item is not None:
            self.tree.setCurrentItem(item)
        conn = self._selected()
        group = self._selected_group()
        menu = QMenu(self)
        if conn:
            menu.addAction(self.a_connect)
            menu.addAction("Als root verbinden (sudo -i)").triggered.connect(lambda: self.connect_selected(sudo=True))
            sub = menu.addMenu("Verbinden mit …")
            for t in self.cfg.terminals:
                a = sub.addAction(t.name)
                a.triggered.connect(lambda _=False, term=t: self.connect_selected(term))
            menu.addSeparator()
            menu.addAction(self.a_edit)
            menu.addAction(self.a_dup)
            menu.addAction(self.a_del)
            menu.addSeparator()
        elif group is not None:
            menu.addAction("Neue Verbindung hier").triggered.connect(lambda: self.new_connection())
            menu.addAction("Neue Untergruppe …").triggered.connect(lambda: self.new_group(group))
            menu.addAction("Umbenennen / verschieben …").triggered.connect(lambda: self.rename_group(group))
            menu.addAction("Gruppe löschen").triggered.connect(lambda: self.delete_group(group))
            menu.addSeparator()
        menu.addAction(self.a_new)
        menu.addAction("Neue Gruppe (oberste Ebene) …").triggered.connect(lambda: self.new_group(""))
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _double_clicked(self, *_):
        if self._selected():
            self.connect_selected()

    # --- Verbindungen ---
    def connect_selected(self, terminal: Terminal | None = None, sudo: bool = False):
        conn = self._selected()
        if not conn:
            return
        if sudo:  # einmalig, wird nicht gespeichert
            conn = replace(conn, sudo_mode=conn.sudo_mode or "login")
        try:
            launcher.launch(self.cfg, conn, terminal)
        except launcher.LaunchError as exc:
            QMessageBox.critical(self, "Verbindung konnte nicht geöffnet werden", str(exc))

    def _apply_password(self, conn: Connection, change: str | None) -> None:
        if change is None:
            return
        try:
            passwords.apply_change(conn, change)
        except passwords.PasswordError as exc:
            QMessageBox.critical(self, "Passwort", str(exc))

    def new_connection(self):
        dlg = ConnectionDialog(self.cfg, parent=self, default_group=self._current_group_context(), save_callback=self._save)
        if dlg.exec():
            conn = dlg.result_connection()
            self._apply_password(conn, dlg.password_change)
            self.cfg.connections.append(conn)
            self._save()
            self.refresh()

    def edit_selected(self):
        group = self._selected_group()
        if group is not None:
            self.rename_group(group)
            return
        conn = self._selected()
        if not conn:
            return
        dlg = ConnectionDialog(self.cfg, conn, parent=self, save_callback=self._save)
        if dlg.exec():
            self._apply_password(conn, dlg.password_change)
            self._save()
            self.refresh()

    def duplicate_selected(self):
        conn = self._selected()
        if not conn:
            return
        clone = copy.deepcopy(conn)
        clone.id = new_id()
        clone.name = f"{conn.name} (Kopie)"
        if conn.use_password:
            try:
                pw = passwords.get_password(conn.id)
                if pw is not None:
                    passwords.set_password(clone.id, pw)
                else:
                    clone.use_password = False
            except passwords.PasswordError as exc:
                clone.use_password = False
                QMessageBox.warning(self, "Passwort nicht kopiert", str(exc))
        self.cfg.connections.append(clone)
        self._save()
        self.refresh()

    def delete_selected(self):
        group = self._selected_group()
        if group is not None:
            self.delete_group(group)
            return
        conn = self._selected()
        if not conn:
            return
        answer = QMessageBox.question(self, "Löschen", f"Verbindung „{conn.name}“ wirklich löschen?")
        if answer == QMessageBox.StandardButton.Yes:
            if conn.use_password:
                try:
                    passwords.delete_password(conn.id)
                except passwords.PasswordError as exc:
                    QMessageBox.warning(self, "Passwort nicht gelöscht", str(exc))
            self.cfg.connections.remove(conn)
            self._save()
            self.refresh()

    # --- Gruppen ---
    def new_group(self, parent: str | None = None):
        if parent is None:
            parent = self._current_group_context()
        title = f"Neue Untergruppe in „{parent}“" if parent else "Neue Gruppe"
        name, ok = QInputDialog.getText(self, title, "Name der Gruppe:")
        if not ok or not name.strip():
            return
        try:
            path = grp.add_group(self.cfg, grp.join(parent, name))
        except ValueError as exc:
            QMessageBox.warning(self, "Gruppe", str(exc))
            return
        self.collapsed.difference_update(grp.ancestors(path))
        self._save()
        self.refresh()

    def rename_group(self, path: str):
        new, ok = QInputDialog.getText(
            self, "Gruppe umbenennen / verschieben", "Neuer Pfad (Untergruppen mit „/“ trennen):", text=path
        )
        if not ok or not new.strip():
            return
        try:
            grp.rename_group(self.cfg, path, new)
        except ValueError as exc:
            QMessageBox.warning(self, "Gruppe", str(exc))
            return
        self._save()
        self.refresh()

    def delete_group(self, path: str):
        n = len(grp.connections_in(self.cfg, path))
        parent = grp.parent_of(path)
        target = f"„{parent}“" if parent else "die oberste Ebene"
        answer = QMessageBox.question(
            self,
            "Gruppe löschen",
            f"Gruppe „{path}“ löschen?\nEnthaltene Verbindungen ({n}) und Untergruppen werden nach {target} verschoben, "
            "keine Verbindung wird gelöscht.",
        )
        if answer == QMessageBox.StandardButton.Yes:
            grp.delete_group(self.cfg, path)
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
            try:
                grp.rename_group(self.cfg, key, grp.join(target, grp.name_of(key)))
            except ValueError as exc:
                QMessageBox.warning(self, "Gruppe", str(exc))
                return
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
            QMessageBox.warning(self, "Synchronisation fehlgeschlagen", str(error))
        elif report is not None and report.conflicts:
            QMessageBox.information(
                self,
                "Synchronisation",
                "Es gab widersprüchliche Änderungen an: " + ", ".join(report.conflicts)
                + ".\nDeine Version blieb erhalten, die andere steht als „… (Konflikt)“ daneben.",
            )

    def edit_sync(self):
        dlg = SyncDialog(self.cfg, self)
        if dlg.exec():
            self._save_no_sync()
            if self.sync.enabled():
                self.sync.sync(manual=True)

    def set_theme(self, mode: str):
        self.cfg.theme = mode
        app = QApplication.instance()
        if app is not None:
            theme.apply(app, mode)
        self._save_no_sync()

    def _save_no_sync(self):
        try:
            storage.save(self.cfg, self.path)
        except OSError as exc:
            QMessageBox.critical(self, "Speichern fehlgeschlagen", str(exc))

    def edit_identities(self):
        IdentitiesDialog(self.cfg, self._save, self).exec()
        self.refresh()

    def import_ssh_config(self):
        found = parse_ssh_config()
        known = {(c.host, c.user, c.port, c.name) for c in self.cfg.connections}
        new = [c for c in found if (c.host, c.user, c.port, c.name) not in known]
        if not new:
            QMessageBox.information(self, "Import", "Keine neuen Einträge in ~/.ssh/config gefunden.")
            return
        self.cfg.connections.extend(new)
        self._save()
        self.refresh()
        QMessageBox.information(self, "Import", f"{len(new)} Verbindung(en) importiert (Gruppe „ssh-config“).")
