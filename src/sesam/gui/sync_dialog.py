from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from .. import passwords, syncing
from ..models import Config

HELP = (
    "Verbindungen, Gruppen und Identitäten werden in einem Git-Repository abgeglichen (eine Datei pro Eintrag). "
    "Passwörter werden nur <b>verschlüsselt</b> abgelegt. Das Sync-Passwort muss auf allen Geräten gleich sein und "
    "<b>kann nicht wiederhergestellt werden</b>. Die Anmeldung am Repository läuft über Git (SSH-Schlüssel oder "
    "gespeicherte Zugangsdaten)."
)


class SyncDialog(QDialog):
    def __init__(self, cfg: Config, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        s = cfg.sync
        self.setWindowTitle("Synchronisation")
        self.setMinimumWidth(520)

        self.enabled = QCheckBox("Synchronisation aktivieren")
        self.enabled.setChecked(s.enabled)
        self.url = QLineEdit(s.url)
        self.url.setPlaceholderText("git@server:gruppe/sesam.git oder https://…")
        self.branch = QLineEdit(s.branch or "main")
        self.auto = QCheckBox("Automatisch abgleichen (beim Start und nach jeder Änderung)")
        self.auto.setChecked(s.auto)
        self.passwords = QCheckBox("Passwörter verschlüsselt mitsynchronisieren")
        self.passwords.setChecked(s.passwords)
        self.phrase = QLineEdit()
        self.phrase.setEchoMode(QLineEdit.EchoMode.Password)
        self.phrase.setPlaceholderText("gespeichert – leer lassen zum Beibehalten" if s.passphrase_saved else "mind. 8 Zeichen")
        self.phrase2 = QLineEdit()
        self.phrase2.setEchoMode(QLineEdit.EchoMode.Password)
        self.phrase2.setPlaceholderText("zur Kontrolle wiederholen")
        self.status = QLabel(f"Letzter Abgleich: {s.last_sync}" if s.last_sync else "Noch nicht abgeglichen.")
        test = QPushButton("Verbindung testen")
        test.clicked.connect(self._test)
        self.test_result = QLabel()
        self.test_result.setWordWrap(True)

        form = QFormLayout()

        form.setVerticalSpacing(8)
        form.addRow("Repository-URL", self.url)
        form.addRow("Branch", self.branch)
        form.addRow("", self.auto)
        form.addRow("", self.passwords)
        form.addRow("Sync-Passwort", self.phrase)
        form.addRow("", self.phrase2)
        row = QWidget()
        r = QHBoxLayout(row)
        r.setContentsMargins(0, 0, 0, 0)
        r.addWidget(test)
        r.addWidget(self.test_result, 1)
        form.addRow("", row)

        help_label = QLabel(HELP)
        help_label.setWordWrap(True)
        help_label.setTextFormat(Qt.TextFormat.RichText)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)
        layout.addWidget(self.enabled)
        layout.addLayout(form)
        layout.addWidget(help_label)
        layout.addWidget(self.status)
        layout.addWidget(buttons)

        self.enabled.toggled.connect(self._update)
        self.passwords.toggled.connect(self._update)
        self._update()

    def _update(self, *_):
        on = self.enabled.isChecked()
        for w in (self.url, self.branch, self.auto, self.passwords):
            w.setEnabled(on)
        pw = on and self.passwords.isChecked()
        self.phrase.setEnabled(pw)
        self.phrase2.setEnabled(pw)

    def _test(self):
        url = self.url.text().strip()
        if not url:
            self.test_result.setText("Bitte zuerst eine URL eintragen.")
            return
        QGuiApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self.test_result.setText(syncing.check_remote(url))
        except syncing.SyncError as exc:
            self.test_result.setText(str(exc))
        finally:
            QGuiApplication.restoreOverrideCursor()

    def _accept(self):
        s = self.cfg.sync
        on = self.enabled.isChecked()
        new_phrase = self.phrase.text()
        if on:
            if not self.url.text().strip():
                QMessageBox.warning(self, "Synchronisation", "Bitte eine Repository-URL eintragen.")
                return
            if self.passwords.isChecked():
                if new_phrase:
                    if len(new_phrase) < 8:
                        QMessageBox.warning(self, "Synchronisation", "Das Sync-Passwort braucht mindestens 8 Zeichen.")
                        return
                    if new_phrase != self.phrase2.text():
                        QMessageBox.warning(self, "Synchronisation", "Die beiden Sync-Passwörter stimmen nicht überein.")
                        return
                elif not s.passphrase_saved:
                    QMessageBox.warning(self, "Synchronisation", "Bitte ein Sync-Passwort für die Verschlüsselung festlegen.")
                    return
        if new_phrase and on and self.passwords.isChecked():
            try:
                passwords.set_password(syncing.PASSPHRASE_KEY, new_phrase)
                s.passphrase_saved = True
            except passwords.PasswordError as exc:
                QMessageBox.critical(self, "Sync-Passwort", str(exc))
                return
        s.enabled = on
        s.url = self.url.text().strip()
        s.branch = self.branch.text().strip() or "main"
        s.auto = self.auto.isChecked()
        s.passwords = self.passwords.isChecked()
        self.accept()
