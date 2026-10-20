from __future__ import annotations

import copy
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QHeaderView, QLineEdit, QMainWindow, QMenu, QMessageBox, QToolBar, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from .. import launcher, passwords, storage
from ..models import Config, Connection, Terminal, new_id
from ..ssh_config import parse_ssh_config
from .connection_dialog import ConnectionDialog
from .terminals_dialog import TerminalsDialog

ID_ROLE = Qt.ItemDataRole.UserRole


class MainWindow(QMainWindow):
    def __init__(self, cfg: Config, path: Path):
        super().__init__()
        self.cfg = cfg
        self.path = path
        self.setWindowTitle("SSH-Verwaltung")
        self.resize(940, 580)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Suchen (Name, Host, Benutzer, Gruppe) …")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.refresh)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Name", "Ziel", "Terminal"])
        self.tree.setAlternatingRowColors(True)
        self.tree.header().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        self.tree.itemDoubleClicked.connect(lambda *_: self.connect_selected())

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.addWidget(self.search)
        layout.addWidget(self.tree)
        self.setCentralWidget(central)

        self._build_actions()
        self.refresh()

    # --- Aufbau ---
    def _build_actions(self):
        def act(text, slot, shortcut=None):
            a = QAction(text, self)
            a.triggered.connect(lambda *_: slot())
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
            return a

        self.a_connect = act("Verbinden", self.connect_selected, "Return")
        self.a_new = act("Neu", self.new_connection, "Ctrl+N")
        self.a_edit = act("Bearbeiten", self.edit_selected, "F2")
        self.a_dup = act("Duplizieren", self.duplicate_selected, "Ctrl+D")
        self.a_del = act("Löschen", self.delete_selected, "Del")
        self.a_terms = act("Terminals …", self.edit_terminals)
        self.a_import = act("Aus ~/.ssh/config importieren", self.import_ssh_config)

        bar = QToolBar("Hauptleiste")
        bar.setMovable(False)
        for a in (self.a_connect, self.a_new, self.a_edit, self.a_dup, self.a_del):
            bar.addAction(a)
        bar.addSeparator()
        bar.addAction(self.a_terms)
        self.addToolBar(bar)

        menu = self.menuBar().addMenu("&Datei")
        menu.addAction(self.a_import)
        menu.addSeparator()
        quit_action = act("Beenden", self.close, "Ctrl+Q")
        menu.addAction(quit_action)
        menu = self.menuBar().addMenu("&Einstellungen")
        menu.addAction(self.a_terms)

    # --- Anzeige ---
    def _terminal_name(self, conn: Connection) -> str:
        t = self.cfg.terminal_by_id(conn.terminal_id) if conn.terminal_id else None
        return t.name if t else "Standard"

    @staticmethod
    def _target(conn: Connection) -> str:
        base = f"{conn.user}@{conn.host}" if conn.user else conn.host
        return f"{base}:{conn.port}" if conn.port != 22 else base

    def refresh(self, *_):
        needle = self.search.text().strip().lower()
        self.tree.clear()
        groups: dict[str, QTreeWidgetItem] = {}
        for conn in sorted(self.cfg.connections, key=lambda c: (c.group.lower(), c.name.lower())):
            hay = " ".join([conn.name, conn.host, conn.user, conn.group]).lower()
            if needle and needle not in hay:
                continue
            item = QTreeWidgetItem([conn.name, self._target(conn), self._terminal_name(conn)])
            item.setData(0, ID_ROLE, conn.id)
            if conn.notes:
                item.setToolTip(0, conn.notes)
            if conn.group:
                parent = groups.get(conn.group)
                if parent is None:
                    parent = QTreeWidgetItem([conn.group])
                    font = parent.font(0)
                    font.setBold(True)
                    parent.setFont(0, font)
                    self.tree.addTopLevelItem(parent)
                    groups[conn.group] = parent
                parent.addChild(item)
            else:
                self.tree.addTopLevelItem(item)
        self.tree.expandAll()

    def _selected(self) -> Connection | None:
        item = self.tree.currentItem()
        cid = item.data(0, ID_ROLE) if item else None
        return self.cfg.connection_by_id(cid) if cid else None

    def _save(self):
        try:
            storage.save(self.cfg, self.path)
        except OSError as exc:
            QMessageBox.critical(self, "Speichern fehlgeschlagen", str(exc))

    def _context_menu(self, pos):
        conn = self._selected()
        menu = QMenu(self)
        if conn:
            menu.addAction(self.a_connect)
            sub = menu.addMenu("Verbinden mit …")
            for t in self.cfg.terminals:
                a = sub.addAction(t.name)
                a.triggered.connect(lambda _=False, term=t: self.connect_selected(term))
            menu.addSeparator()
            menu.addAction(self.a_edit)
            menu.addAction(self.a_dup)
            menu.addAction(self.a_del)
            menu.addSeparator()
        menu.addAction(self.a_new)
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    # --- Aktionen ---
    def connect_selected(self, terminal: Terminal | None = None):
        conn = self._selected()
        if not conn:
            return
        try:
            launcher.launch(self.cfg, conn, terminal)
        except launcher.LaunchError as exc:
            QMessageBox.critical(self, "Verbindung konnte nicht geöffnet werden", str(exc))

    def _apply_password(self, conn: Connection, change: str | None) -> None:
        if change is None:
            return
        try:
            if change:
                passwords.set_password(conn.id, change)
                conn.use_password = True
            else:
                passwords.delete_password(conn.id)
                conn.use_password = False
        except passwords.PasswordError as exc:
            QMessageBox.critical(self, "Passwort", str(exc))

    def new_connection(self):
        dlg = ConnectionDialog(self.cfg, parent=self)
        if dlg.exec():
            conn = dlg.result_connection()
            self._apply_password(conn, dlg.password_change)
            self.cfg.connections.append(conn)
            self._save()
            self.refresh()

    def edit_selected(self):
        conn = self._selected()
        if not conn:
            return
        dlg = ConnectionDialog(self.cfg, conn, parent=self)
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
        QMessageBox.information(self, "Import", f"{len(new)} Verbindung(en) importiert.")
