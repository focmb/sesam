from __future__ import annotations

import copy

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel,
    QFileDialog, QLineEdit, QListWidget, QMenu, QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout,
)

from .. import launcher
from ..terminals import template_terminals
from ..models import Config, Connection, Terminal

HELP = (
    "<b>Platzhalter:</b><br>"
    "<code>{ssh_args}</code> – ssh-Befehl als einzelne Argumente (für Terminals mit <code>-e</code>/<code>--</code>)<br>"
    "<code>{ssh_command}</code> – ssh-Befehl als ein String (für <code>cmd /k</code>, <code>-Command</code>)<br>"
    "<code>{title}</code> <code>{host}</code> <code>{user}</code> <code>{port}</code> <code>{destination}</code>"
)

PLATFORMS = [("Alle", "all"), ("Linux", "linux"), ("Windows", "windows")]


class TerminalsDialog(QDialog):
    """Konfiguration der Terminals, in denen Verbindungen geöffnet werden."""

    def __init__(self, cfg: Config, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Terminals konfigurieren")
        self.resize(720, 460)
        self.terminals: list[Terminal] = copy.deepcopy(cfg.terminals)
        self.default_id = cfg.default_terminal_id
        self._current: Terminal | None = None

        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._select)
        add = QPushButton("Neu ▾")
        add_menu = QMenu(add)
        add_menu.addAction("Eigenes Terminal (leer)").triggered.connect(lambda: self._add())
        add_menu.addSeparator()
        for tpl in template_terminals():
            add_menu.addAction(f"Vorlage: {tpl.name}").triggered.connect(lambda _=False, t=tpl: self._add(t))
        add.setMenu(add_menu)
        remove = QPushButton("Löschen")
        remove.clicked.connect(self._remove)
        make_default = QPushButton("Als Standard")
        make_default.clicked.connect(self._make_default)
        left_buttons = QHBoxLayout()
        for b in (add, remove):
            left_buttons.addWidget(b)
        left = QVBoxLayout()
        left.addWidget(self.list)
        left.addLayout(left_buttons)
        left.addWidget(make_default)

        self.name = QLineEdit()
        self.command = QLineEdit()
        self.command.setPlaceholderText("z. B. /opt/ghostty/ghostty --title={title} -e {ssh_args}")
        pick = QPushButton("Programm wählen …")
        pick.clicked.connect(self._pick_program)
        self.platform = QComboBox()
        for label, value in PLATFORMS:
            self.platform.addItem(label, value)
        self.new_console = QCheckBox("Eigenes Konsolenfenster öffnen (Windows: cmd, PowerShell)")
        for w in (self.name, self.command):
            w.textEdited.connect(self._store)
        self.platform.currentIndexChanged.connect(self._store)
        self.new_console.toggled.connect(self._store)
        test = QPushButton("Testen (öffnet Terminal mit ssh localhost)")
        test.clicked.connect(self._test)

        form = QFormLayout()

        form.setVerticalSpacing(8)
        form.addRow("Name", self.name)
        form.addRow("Befehlsvorlage", self.command)
        form.addRow("", pick)
        form.addRow("Plattform", self.platform)
        form.addRow("", self.new_console)
        help_label = QLabel(HELP)
        help_label.setWordWrap(True)
        help_label.setTextFormat(Qt.TextFormat.RichText)
        right = QVBoxLayout()
        right.addLayout(form)
        right.addWidget(help_label)
        right.addWidget(test)
        right.addStretch(1)

        body = QHBoxLayout()
        body.addLayout(left, 1)
        body.addLayout(right, 2)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)
        layout.addLayout(body)
        layout.addWidget(buttons)

        self._reload_list()
        if self.terminals:
            self.list.setCurrentRow(0)
        else:
            self._select(-1)

    # --- Liste ---
    def _label(self, t: Terminal) -> str:
        return f"{t.name} (Standard)" if t.id == self.default_id else t.name

    def _reload_list(self, keep_row: int | None = None):
        row = self.list.currentRow() if keep_row is None else keep_row
        self.list.blockSignals(True)
        self.list.clear()
        for t in self.terminals:
            self.list.addItem(QListWidgetItem(self._label(t)))
        self.list.blockSignals(False)
        if 0 <= row < len(self.terminals):
            self.list.setCurrentRow(row)

    def _select(self, row: int):
        self._current = self.terminals[row] if 0 <= row < len(self.terminals) else None
        enabled = self._current is not None
        for w in (self.name, self.command, self.platform, self.new_console):
            w.setEnabled(enabled)
            w.blockSignals(True)
        t = self._current or Terminal("", "")
        self.name.setText(t.name)
        self.command.setText(t.command)
        self.platform.setCurrentIndex(max(self.platform.findData(t.platform), 0))
        self.new_console.setChecked(t.new_console)
        for w in (self.name, self.command, self.platform, self.new_console):
            w.blockSignals(False)

    def _store(self, *_):
        t = self._current
        if t is None:
            return
        t.name = self.name.text()
        t.command = self.command.text()
        t.platform = self.platform.currentData()
        t.new_console = self.new_console.isChecked()
        item = self.list.currentItem()
        if item:
            item.setText(self._label(t))

    def _pick_program(self):
        if not self._current:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Terminal-Programm wählen")
        if not path:
            return
        quoted = f'"{path}"' if " " in path else path
        rest = launcher.split_command(self.command.text())[1:]
        self.command.setText(" ".join([quoted] + [launcher.join_command([r]) for r in rest]) if rest else f"{quoted} {{ssh_args}}")
        self._store()

    def _add(self, template: Terminal | None = None):
        t = template or Terminal("Eigenes Terminal", "")
        self.terminals.append(t)
        if not self.default_id:
            self.default_id = t.id
        self._reload_list(keep_row=len(self.terminals) - 1)

    def _remove(self):
        row = self.list.currentRow()
        if row < 0:
            return
        removed = self.terminals.pop(row)
        if removed.id == self.default_id:
            self.default_id = self.terminals[0].id if self.terminals else ""
        self._reload_list(keep_row=min(row, len(self.terminals) - 1))
        self._select(self.list.currentRow())

    def _make_default(self):
        if self._current:
            self.default_id = self._current.id
            self._reload_list()

    def _test(self):
        if not self._current:
            return
        probe = Connection(name="Terminal-Test", host="localhost")
        cfg = Config(terminals=[self._current], default_terminal_id=self._current.id)
        try:
            launcher.launch(cfg, probe, self._current)
        except launcher.LaunchError as exc:
            QMessageBox.critical(self, "Terminal-Test fehlgeschlagen", str(exc))

    def result_values(self) -> tuple[list[Terminal], str]:
        default = self.default_id if any(t.id == self.default_id for t in self.terminals) else ""
        if not default and self.terminals:
            default = self.terminals[0].id
        return self.terminals, default
