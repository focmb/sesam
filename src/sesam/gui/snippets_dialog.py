"""Snippets verwalten und auf Verbindungen ausführen."""
from __future__ import annotations

import copy
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QPlainTextEdit, QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
)

from .. import groups as grp
from .. import snippets
from ..models import SNIPPET_AFTER, Config, Connection, Snippet, new_id

AFTER_LABELS = {
    "shell": "Shell offen lassen",
    "wait": "Auf eine Taste warten, dann schließen",
    "close": "Terminal sofort schließen",
}
SNIPPET_ROLE = int(Qt.ItemDataRole.UserRole)


def monospace() -> QFont:
    return QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)


class SnippetEditDialog(QDialog):
    """Ein Snippet anlegen oder bearbeiten."""

    def __init__(self, cfg: Config, snippet: Snippet | None = None, parent=None):
        super().__init__(parent)
        self.snippet = snippet or Snippet(name="")
        self.setWindowTitle("Snippet bearbeiten" if snippet else "Neues Snippet")
        self.setMinimumWidth(520)
        self.name = QLineEdit(self.snippet.name)
        self.name.setPlaceholderText("z. B. Speicherplatz prüfen")
        self.category = QComboBox()
        self.category.setEditable(True)
        self.category.addItems(snippets.categories(cfg))
        self.category.setCurrentText(self.snippet.category)
        self.category.lineEdit().setPlaceholderText("optional, Unterordner mit „/“ (z. B. Docker/Logs)")
        self.command = QPlainTextEdit(self.snippet.command)
        self.command.setFont(monospace())
        self.command.setPlaceholderText("df -h && free -m")
        self.command.setMinimumHeight(110)
        self.command.setTabChangesFocus(True)
        self.sudo = QCheckBox("Mit sudo ausführen")
        self.sudo.setChecked(self.snippet.sudo)
        self.sudo.setToolTip("Nur der Befehl läuft per sudo -i, eine danach offene Shell gehört wieder dem normalen Benutzer. "
                             "Das gespeicherte Passwort wird wie bei „Mit sudo“ der Verbindung verwendet.")
        self.after = QComboBox()
        for key in SNIPPET_AFTER:
            self.after.addItem(AFTER_LABELS[key], key)
        self.after.setCurrentIndex(max(self.after.findData(self.snippet.after), 0))
        self.notes = QLineEdit(self.snippet.notes)
        form = QFormLayout()
        form.setVerticalSpacing(8)
        form.addRow("Name*", self.name)
        form.addRow("Kategorie", self.category)
        form.addRow("Befehl*", self.command)
        form.addRow("", self.sudo)
        form.addRow("Danach", self.after)
        form.addRow("Notiz", self.notes)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def _accept(self):
        if not self.name.text().strip() or not self.command.toPlainText().strip():
            QMessageBox.warning(self, "Eingabe unvollständig", "Name und Befehl sind Pflichtfelder.")
            return
        s = self.snippet
        s.name = self.name.text().strip()
        s.category = grp.normalize(self.category.currentText())
        s.command = self.command.toPlainText().strip("\n")
        s.sudo = self.sudo.isChecked()
        s.after = self.after.currentData() or "shell"
        s.notes = self.notes.text().strip()
        self.accept()


class SnippetsDialog(QDialog):
    """Verwaltung aller Snippets. Änderungen werden sofort über save_callback gespeichert.

    ``run_callback(snippet, verbindungen)`` startet die Ausführung (Rückfrage und Fehlermeldungen übernimmt der Aufrufer).
    """

    def __init__(self, cfg: Config, save_callback: Callable[[], None], run_callback: Callable[[Snippet, list[Connection]], None],
                 preselect: list[str] | None = None, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.save_callback = save_callback
        self.run_callback = run_callback
        self.setWindowTitle("Snippets")
        self.resize(760, 520)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(18)
        self.tree.currentItemChanged.connect(lambda *_: self._show())
        self.tree.itemDoubleClicked.connect(lambda *_: self._edit())
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setFont(monospace())
        self.info = QLabel()
        self.info.setWordWrap(True)
        self.targets = QListWidget()
        self.targets.itemChanged.connect(lambda *_: self._update_buttons())
        for conn in sorted(cfg.connections, key=lambda c: (c.group.lower(), c.name.lower())):
            label = f"{conn.group + ' / ' if conn.group else ''}{conn.name}"
            item = QListWidgetItem(label)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if conn.id in (preselect or []) else Qt.CheckState.Unchecked)
            item.setData(SNIPPET_ROLE, conn.id)
            self.targets.addItem(item)

        def button(text, slot):
            b = QPushButton(text)
            b.clicked.connect(slot)
            return b

        self.btn_new = button("Neu …", self._new)
        self.btn_edit = button("Bearbeiten …", self._edit)
        self.btn_dup = button("Duplizieren", self._duplicate)
        self.btn_delete = button("Löschen", self._delete)
        self.btn_all = button("Alle", lambda: self._check_all(True))
        self.btn_none = button("Keine", lambda: self._check_all(False))
        self.btn_run = button("Ausführen", self._run)
        self.btn_run.setDefault(True)
        self.btn_run.setMinimumWidth(280)  # „Auf 12 Verbindungen ausführen“ muss ganz passen
        top = QHBoxLayout()
        for b in (self.btn_new, self.btn_edit, self.btn_dup, self.btn_delete):
            top.addWidget(b)
        top.addStretch(1)
        left = QVBoxLayout()
        left.addWidget(self.tree, 1)
        right = QVBoxLayout()
        right.addWidget(self.info)
        right.addWidget(self.detail, 1)
        body = QHBoxLayout()
        body.addLayout(left, 2)
        body.addLayout(right, 3)
        run_head = QHBoxLayout()
        run_head.addWidget(QLabel("Ausführen auf:"))
        run_head.addStretch(1)
        run_head.addWidget(self.btn_all)
        run_head.addWidget(self.btn_none)
        self.targets.setMaximumHeight(130)
        run_row = QHBoxLayout()
        run_row.addStretch(1)
        run_row.addWidget(self.btn_run)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        close.accepted.connect(self.accept)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(10)
        layout.addLayout(top)
        layout.addLayout(body, 1)
        layout.addLayout(run_head)
        layout.addWidget(self.targets)
        layout.addLayout(run_row)
        layout.addWidget(close)
        self._reload()

    # --- Anzeige ---
    def _reload(self, select: str | None = None):
        self.tree.blockSignals(True)
        self.tree.clear()
        nodes: dict[str, QTreeWidgetItem] = {}

        def parent_item(category: str):
            if not category:
                return self.tree.invisibleRootItem()
            if category not in nodes:
                node = QTreeWidgetItem([grp.name_of(category)])
                font = node.font(0)
                font.setBold(True)
                node.setFont(0, font)
                node.setFlags(node.flags() & ~Qt.ItemFlag.ItemIsDragEnabled)
                parent_item(grp.parent_of(category)).addChild(node)
                nodes[category] = node
            return nodes[category]

        for cat in snippets.categories(self.cfg):
            parent_item(cat)
        for cat in ["", *snippets.categories(self.cfg)]:
            for sn in snippets.in_category(self.cfg, cat):
                item = QTreeWidgetItem([sn.name])
                item.setData(0, SNIPPET_ROLE, sn.id)
                parent_item(cat).addChild(item)
                if sn.id == select:
                    self.tree.setCurrentItem(item)
        self.tree.expandAll()
        self.tree.blockSignals(False)
        if select is None and self._current() is None:
            first = self._first_snippet_item()
            if first is not None:
                self.tree.setCurrentItem(first)
        self._show()

    def _first_snippet_item(self):
        from PySide6.QtWidgets import QTreeWidgetItemIterator

        it = QTreeWidgetItemIterator(self.tree)
        while it.value():
            if it.value().data(0, SNIPPET_ROLE):
                return it.value()
            it += 1
        return None

    def _current(self) -> Snippet | None:
        item = self.tree.currentItem()
        sid = item.data(0, SNIPPET_ROLE) if item else None
        return self.cfg.snippet_by_id(sid) if sid else None

    def _show(self):
        sn = self._current()
        if sn is None:
            self.detail.setPlainText("")
            self.info.setText("Noch keine Snippets. Mit „Neu …“ einen Befehl anlegen." if not self.cfg.snippets else "Snippet auswählen.")
        else:
            parts = ["mit sudo" if sn.sudo else "ohne sudo", AFTER_LABELS[sn.after]]
            if sn.notes:
                parts.append(sn.notes)
            self.info.setText(" · ".join(parts))
            self.detail.setPlainText(sn.command)
        for b in (self.btn_edit, self.btn_dup, self.btn_delete):
            b.setEnabled(sn is not None)
        self._update_buttons()

    def _checked(self) -> list[Connection]:
        out = []
        for i in range(self.targets.count()):
            item = self.targets.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                conn = self.cfg.connection_by_id(item.data(SNIPPET_ROLE))
                if conn is not None:
                    out.append(conn)
        return out

    def _update_buttons(self):
        n = len(self._checked())
        self.btn_run.setEnabled(self._current() is not None and n > 0)
        self.btn_run.setText("Ausführen" if n <= 1 else f"Auf {n} Verbindungen ausführen")

    def _check_all(self, on: bool):
        for i in range(self.targets.count()):
            self.targets.item(i).setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)

    # --- Aktionen ---
    def _changed(self, select: str | None = None):
        self.save_callback()
        self._reload(select)

    def _new(self):
        dlg = SnippetEditDialog(self.cfg, parent=self)
        if dlg.exec():
            self.cfg.snippets.append(dlg.snippet)
            self._changed(dlg.snippet.id)

    def _edit(self):
        sn = self._current()
        if sn is None:
            return
        dlg = SnippetEditDialog(self.cfg, sn, self)
        if dlg.exec():
            self._changed(sn.id)

    def _duplicate(self):
        sn = self._current()
        if sn is None:
            return
        clone = copy.deepcopy(sn)
        clone.id = new_id()
        clone.name = f"{sn.name} (Kopie)"
        self.cfg.snippets.append(clone)
        self._changed(clone.id)

    def _delete(self):
        sn = self._current()
        if sn is None:
            return
        if QMessageBox.question(self, "Löschen", f"Snippet „{sn.name}“ wirklich löschen?") != QMessageBox.StandardButton.Yes:
            return
        self.cfg.snippets.remove(sn)
        self._changed()

    def _run(self):
        sn = self._current()
        conns = self._checked()
        if sn is not None and conns:
            self.run_callback(sn, conns)
