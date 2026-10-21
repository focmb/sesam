from __future__ import annotations

from typing import Callable

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout, QLineEdit,
    QListWidget, QMessageBox, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget,
)

from .. import passwords
from ..models import Config, Identity
from .keys_dialog import fill_key_combo


class IdentityEditDialog(QDialog):
    """Eine Identität anlegen oder bearbeiten."""

    def __init__(self, ident: Identity | None = None, parent=None, cfg: Config | None = None):
        super().__init__(parent)
        self.cfg = cfg or Config()
        self.ident = ident or Identity(name="")
        self.password_change: str | None = None  # None = unverändert, "" = löschen, sonst neues Passwort
        self.setWindowTitle("Identität bearbeiten" if ident else "Neue Identität")
        self.setMinimumWidth(440)

        self.name = QLineEdit(self.ident.name)
        self.name.setPlaceholderText("z. B. root@Kunde A")
        self.user = QLineEdit(self.ident.user)
        self.key = QLineEdit(self.ident.identity_file)
        self.key_combo = QComboBox()
        fill_key_combo(self.key_combo, self.cfg, self.ident.key_id)
        self.key_combo.currentIndexChanged.connect(self._key_changed)
        browse = QPushButton("…")
        self._browse_btn = browse
        browse.setFixedWidth(40)
        browse.clicked.connect(self._browse)
        key_row = QWidget()
        row = QHBoxLayout(key_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.key)
        row.addWidget(browse)
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText("gespeichert – leer lassen zum Beibehalten" if self.ident.use_password else "optional")
        self.password.setToolTip("Wird im Schlüsselbund des Betriebssystems gespeichert, nicht in der Konfigurationsdatei.")
        self.clear_password = QCheckBox("Gespeichertes Passwort löschen")
        self.clear_password.setVisible(self.ident.use_password)
        self.notes = QPlainTextEdit(self.ident.notes)
        self.notes.setFixedHeight(60)

        form = QFormLayout()

        form.setVerticalSpacing(8)
        form.addRow("Name*", self.name)
        form.addRow("Benutzer", self.user)
        form.addRow("Gespeicherter Schlüssel", self.key_combo)
        form.addRow("Schlüsseldatei", key_row)
        form.addRow("Passwort", self.password)
        form.addRow("", self.clear_password)
        form.addRow("Notizen", self.notes)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)
        layout.addLayout(form)
        layout.addWidget(buttons)
        self._key_changed()

    def _key_changed(self, *_):
        own = not self.key_combo.currentData()
        self.key.setEnabled(own)
        self._browse_btn.setEnabled(own)

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(self, "Schlüsseldatei wählen")
        if path:
            self.key.setText(path)

    def _accept(self):
        if not self.name.text().strip():
            QMessageBox.warning(self, "Eingabe unvollständig", "Der Name ist ein Pflichtfeld.")
            return
        i = self.ident
        i.name = self.name.text().strip()
        i.user = self.user.text().strip()
        i.key_id = self.key_combo.currentData() or ""
        i.identity_file = "" if i.key_id else self.key.text().strip()
        i.notes = self.notes.toPlainText().strip()
        if self.password.text():
            self.password_change = self.password.text()
        elif self.clear_password.isChecked():
            self.password_change = ""
        self.accept()


def save_identity(parent, ident: Identity, change: str | None) -> bool:
    """Wendet die Passwortänderung an; zeigt bei Fehlern eine Meldung. Rückgabe: Passwort-Teil erfolgreich."""
    try:
        passwords.apply_change(ident, change)
        return True
    except passwords.PasswordError as exc:
        QMessageBox.critical(parent, "Passwort", str(exc))
        return False


class IdentitiesDialog(QDialog):
    """Verwaltung aller Identitäten. Änderungen werden sofort über save_callback gespeichert."""

    def __init__(self, cfg: Config, save_callback: Callable[[], None], parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.save_callback = save_callback
        self.setWindowTitle("Identitäten")
        self.resize(460, 360)
        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(lambda *_: self._edit())
        new, edit, delete = QPushButton("Neu …"), QPushButton("Bearbeiten …"), QPushButton("Löschen")
        new.clicked.connect(self._new)
        edit.clicked.connect(self._edit)
        delete.clicked.connect(self._delete)
        buttons = QHBoxLayout()
        for b in (new, edit, delete):
            buttons.addWidget(b)
        buttons.addStretch(1)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        close.accepted.connect(self.accept)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)
        layout.addWidget(self.list)
        layout.addLayout(buttons)
        layout.addWidget(close)
        self._reload()

    def _usage(self, ident: Identity) -> int:
        return sum(1 for c in self.cfg.connections if c.identity_id == ident.id)

    def _reload(self, select: str | None = None):
        self.list.clear()
        for ident in sorted(self.cfg.identities, key=lambda i: i.name.lower()):
            parts = [ident.user or "—"]
            if ident.identity_file or ident.key_id:
                parts.append("Schlüssel")
            if ident.use_password:
                parts.append("Passwort")
            self.list.addItem(f"{ident.name}   ({', '.join(parts)}; {self._usage(ident)} Verbindung(en))")
            self.list.item(self.list.count() - 1).setData(256, ident.id)
            if ident.id == select:
                self.list.setCurrentRow(self.list.count() - 1)

    def _current(self) -> Identity | None:
        item = self.list.currentItem()
        return self.cfg.identity_by_id(item.data(256)) if item else None

    def _new(self):
        dlg = IdentityEditDialog(parent=self, cfg=self.cfg)
        if dlg.exec():
            save_identity(self, dlg.ident, dlg.password_change)
            self.cfg.identities.append(dlg.ident)
            self.save_callback()
            self._reload(dlg.ident.id)

    def _edit(self):
        ident = self._current()
        if not ident:
            return
        dlg = IdentityEditDialog(ident, self, cfg=self.cfg)
        if dlg.exec():
            save_identity(self, ident, dlg.password_change)
            self.save_callback()
            self._reload(ident.id)

    def _delete(self):
        ident = self._current()
        if not ident:
            return
        n = self._usage(ident)
        text = f"Identität „{ident.name}“ löschen?"
        if n:
            text += f"\n{n} Verbindung(en) verwenden sie und müssen danach wieder eigene Zugangsdaten bekommen."
        if QMessageBox.question(self, "Löschen", text) != QMessageBox.StandardButton.Yes:
            return
        if ident.use_password:
            try:
                passwords.delete_password(ident.id)
            except passwords.PasswordError as exc:
                QMessageBox.warning(self, "Passwort nicht gelöscht", str(exc))
        for c in self.cfg.connections:
            if c.identity_id == ident.id:
                c.identity_id = ""
        self.cfg.identities.remove(ident)
        self.save_callback()
        self._reload()
