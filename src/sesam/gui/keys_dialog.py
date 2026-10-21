"""Schlüsselverwaltung: erzeugen, importieren, ansehen, auf Server installieren, löschen."""
from __future__ import annotations

import getpass
import os
import socket
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout, QInputDialog, QLabel,
    QLineEdit, QListWidget, QMessageBox, QPlainTextEdit, QPushButton, QVBoxLayout,
)

from .. import keys, keystore, launcher, passwords, sftp
from ..models import Config, SshKey
from .jobs import Job


def fill_key_combo(combo: QComboBox, cfg: Config, select: str = "", none_text: str = "Keiner (Schlüsseldatei oder Passwort)") -> None:
    """Füllt eine Auswahl mit den gespeicherten Schlüsseln; ``itemData`` ist die Schlüssel-ID."""
    combo.blockSignals(True)
    combo.clear()
    combo.addItem(none_text, "")
    for key in sorted(cfg.ssh_keys, key=lambda k: k.name.lower()):
        combo.addItem(f"{key.name}  ({key.type})", key.id)
    combo.setCurrentIndex(max(combo.findData(select), 0))
    combo.blockSignals(False)


def default_comment() -> str:
    try:
        return f"{getpass.getuser()}@{socket.gethostname()}"
    except Exception:  # noqa: BLE001 - getuser kann in Sonderumgebungen scheitern
        return "sesam"


def store_key(parent, cfg: Config, entry: SshKey, private: str) -> bool:
    """Legt den privaten Teil ab und hängt den Eintrag an die Konfiguration. Zeigt Fehler als Meldung."""
    try:
        keystore.FileStore().set(entry.id, private)
    except passwords.PasswordError as exc:
        QMessageBox.critical(parent, "Schlüssel", str(exc))
        return False
    cfg.ssh_keys.append(entry)
    return True


class GenerateKeyDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Neuen Schlüssel erzeugen")
        self.setMinimumWidth(420)
        self.name = QLineEdit()
        self.name.setPlaceholderText("z. B. Arbeit")
        self.type = QComboBox()
        self.type.addItem("Ed25519 (empfohlen)", "ed25519")
        self.type.addItem("RSA 4096 (kompatibel mit sehr alten Servern)", "rsa")
        self.comment = QLineEdit(default_comment())
        self.passphrase = QLineEdit()
        self.passphrase.setEchoMode(QLineEdit.EchoMode.Password)
        self.passphrase.setPlaceholderText("optional, schützt den Schlüssel")
        self.repeat = QLineEdit()
        self.repeat.setEchoMode(QLineEdit.EchoMode.Password)
        self.repeat.setPlaceholderText("Passphrase wiederholen")
        form = QFormLayout()
        form.addRow("Name*", self.name)
        form.addRow("Typ", self.type)
        form.addRow("Kommentar", self.comment)
        form.addRow("Passphrase", self.passphrase)
        form.addRow("", self.repeat)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)
        layout.addLayout(form)
        layout.addWidget(buttons)
        self.material: keys.KeyMaterial | None = None

    def _accept(self):
        if not self.name.text().strip():
            QMessageBox.warning(self, "Eingabe unvollständig", "Der Name ist ein Pflichtfeld.")
            return
        if self.passphrase.text() != self.repeat.text():
            QMessageBox.warning(self, "Passphrase", "Die beiden Passphrasen stimmen nicht überein.")
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self.material = keys.generate(self.type.currentData(), self.passphrase.text() or None)
        except keys.KeyManagementError as exc:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Schlüssel", str(exc))
            return
        QApplication.restoreOverrideCursor()
        self.accept()

    def entry(self) -> SshKey:
        assert self.material is not None
        return keys.new_entry(self.name.text(), self.material, self.comment.text())


class InstallKeyDialog(QDialog):
    """Trägt den öffentlichen Schlüssel auf einem Server ein (wie ssh-copy-id)."""

    def __init__(self, cfg: Config, key: SshKey, parent=None):
        super().__init__(parent)
        self.cfg, self.key = cfg, key
        self.setWindowTitle(f"„{key.name}“ auf Server installieren")
        self.setMinimumWidth(460)
        self.target = QComboBox()
        for conn in sorted(cfg.connections, key=lambda c: c.name.lower()):
            eff, _ = launcher.resolve(cfg, conn)
            self.target.addItem(f"{conn.name}  ({eff.user + '@' if eff.user else ''}{eff.host})", conn.id)
        self.status = QLabel("Der öffentliche Schlüssel wird in ~/.ssh/authorized_keys eingetragen. "
                             "Dafür muss die Anmeldung einmalig klappen (Passwort oder anderer Schlüssel).")
        self.status.setWordWrap(True)
        self.install_btn = QPushButton("Installieren")
        self.install_btn.clicked.connect(self._install)
        self.install_btn.setEnabled(self.target.count() > 0)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        close.accepted.connect(self.accept)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)
        layout.addWidget(QLabel("Verbindung:"))
        layout.addWidget(self.target)
        layout.addWidget(self.status)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.install_btn)
        layout.addLayout(row)
        layout.addWidget(close)
        self._job: Job | None = None

    def _install(self, password: str | None = None):
        conn = self.cfg.connection_by_id(self.target.currentData())
        if conn is None:
            return
        self.install_btn.setEnabled(False)
        self.target.setEnabled(False)
        self.status.setText("Verbinde …")
        line = self.key.authorized_line
        self._job = Job(lambda job: keys.install_public_key(self.cfg, conn, line, password=password), self)
        self._job.result.connect(self._done)
        self._job.failed.connect(self._failed)
        self._job.start()

    def _done(self, added: bool):
        self._finish("Schlüssel wurde eingetragen." if added else "Der Schlüssel war dort bereits eingetragen.")

    def _failed(self, exc):
        if isinstance(exc, sftp.SftpAuthError):
            text, ok = QInputDialog.getText(self, "Passwort", "Passwort für die Anmeldung:", QLineEdit.EchoMode.Password)
            if ok and text:
                self._install(text)
                return
            self._finish("Anmeldung abgebrochen.")
        else:
            self._finish(f"Fehlgeschlagen: {exc}")

    def _finish(self, text: str):
        self.status.setText(text)
        self.install_btn.setEnabled(True)
        self.target.setEnabled(True)


class KeysDialog(QDialog):
    """Verwaltung aller Schlüssel. Änderungen werden sofort über save_callback gespeichert."""

    def __init__(self, cfg: Config, save_callback: Callable[[], None], parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.save_callback = save_callback
        self.setWindowTitle("SSH-Schlüssel")
        self.resize(640, 420)
        self.list = QListWidget()
        self.list.currentRowChanged.connect(lambda *_: self._show())
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)

        def button(text, slot):
            b = QPushButton(text)
            b.clicked.connect(slot)
            return b

        self.btn_copy = button("Öffentlichen Schlüssel kopieren", self._copy)
        self.btn_install = button("Auf Server installieren …", self._install)
        self.btn_delete = button("Löschen", self._delete)
        top = QHBoxLayout()
        top.addWidget(button("Erzeugen …", self._generate))
        top.addWidget(button("Importieren …", self._import))
        top.addStretch(1)
        actions = QHBoxLayout()
        actions.addWidget(self.btn_copy)
        actions.addWidget(self.btn_install)
        actions.addStretch(1)
        actions.addWidget(self.btn_delete)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        close.accepted.connect(self.accept)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)
        layout.addLayout(top)
        body = QHBoxLayout()
        body.addWidget(self.list, 2)
        body.addWidget(self.detail, 3)
        layout.addLayout(body, 1)
        layout.addLayout(actions)
        layout.addWidget(close)
        self._reload()

    def _reload(self, select: str | None = None):
        self.list.blockSignals(True)
        self.list.clear()
        for key in sorted(self.cfg.ssh_keys, key=lambda k: k.name.lower()):
            self.list.addItem(f"{key.name}   ({key.type})")
            self.list.item(self.list.count() - 1).setData(256, key.id)
            if key.id == select:
                self.list.setCurrentRow(self.list.count() - 1)
        if self.list.currentRow() < 0 and self.list.count():
            self.list.setCurrentRow(0)
        self.list.blockSignals(False)
        self._show()

    def _current(self) -> SshKey | None:
        item = self.list.currentItem()
        return self.cfg.ssh_key_by_id(item.data(256)) if item else None

    def _show(self):
        key = self._current()
        for b in (self.btn_copy, self.btn_install, self.btn_delete):
            b.setEnabled(key is not None)
        if key is None:
            self.detail.setPlainText("Noch keine Schlüssel. Mit „Erzeugen …“ oder „Importieren …“ anlegen.")
            return
        users = keys.users_of(self.cfg, key.id)
        local = keystore.FileStore().exists(key.id)
        lines = [
            f"Name: {key.name}",
            f"Typ: {key.type}" + (" · mit Passphrase geschützt" if key.encrypted else ""),
            f"Fingerabdruck: {key.fingerprint}",
            f"Kommentar: {key.comment or '—'}",
            f"Erstellt: {key.created or '—'}",
            "Privater Schlüssel auf diesem Gerät: " + ("ja" if local else "nein (Passwort-Synchronisation aktivieren, um ihn zu übertragen)"),
            "Verwendet von: " + (", ".join(users) if users else "—"),
            "",
            "Öffentlicher Schlüssel:",
            key.authorized_line,
        ]
        self.detail.setPlainText("\n".join(lines))

    def _changed(self, select: str | None = None):
        self.save_callback()
        self._reload(select)

    def _generate(self):
        dlg = GenerateKeyDialog(self)
        if not dlg.exec():
            return
        entry = dlg.entry()
        if store_key(self, self.cfg, entry, dlg.material.private):
            self._changed(entry.id)

    def _import(self):
        path, _ = QFileDialog.getOpenFileName(self, "Privaten Schlüssel wählen")
        if not path:
            return
        passphrase = None
        while True:
            try:
                material = keys.read_key_file(path, passphrase)
                break
            except keys.NeedsPassphrase:
                passphrase, ok = QInputDialog.getText(
                    self, "Passphrase", "Der Schlüssel ist geschützt. Passphrase (nur zum Auslesen des öffentlichen Teils):",
                    QLineEdit.EchoMode.Password)
                if not ok:
                    return
            except keys.KeyManagementError as exc:
                QMessageBox.critical(self, "Importieren", str(exc))
                return
        default = os.path.basename(path)
        name, ok = QInputDialog.getText(self, "Name", "Name des Schlüssels:", QLineEdit.EchoMode.Normal, default)
        if not ok or not name.strip():
            return
        comment = material.public.split(None, 2)[2] if len(material.public.split(None, 2)) > 2 else ""
        entry = keys.new_entry(name, material, comment or default_comment())
        try:
            with open(path, encoding="utf-8") as f:
                private = f.read()
        except OSError as exc:
            QMessageBox.critical(self, "Importieren", str(exc))
            return
        if store_key(self, self.cfg, entry, private):
            self._changed(entry.id)

    def _copy(self):
        key = self._current()
        if key:
            QGuiApplication.clipboard().setText(key.authorized_line)

    def _install(self):
        key = self._current()
        if key is None:
            return
        if not self.cfg.connections:
            QMessageBox.information(self, "Installieren", "Es gibt noch keine Verbindung, auf die der Schlüssel installiert werden könnte.")
            return
        InstallKeyDialog(self.cfg, key, self).exec()

    def _delete(self):
        key = self._current()
        if key is None:
            return
        users = keys.users_of(self.cfg, key.id)
        text = f"Schlüssel „{key.name}“ löschen?\nDer private Teil wird auf diesem Gerät und – bei aktiver Synchronisation – auf allen Geräten entfernt."
        if users:
            text += "\n\nNoch zugewiesen an: " + ", ".join(users) + "\nDie Zuweisung wird aufgehoben."
        text += "\n\nAuf Servern eingetragene öffentliche Schlüssel bleiben dort bestehen."
        if QMessageBox.question(self, "Löschen", text) != QMessageBox.StandardButton.Yes:
            return
        try:
            keystore.FileStore().delete(key.id)
        except passwords.PasswordError as exc:
            QMessageBox.critical(self, "Löschen", str(exc))
            return
        keys.forget(self.cfg, key.id)
        self._changed()
