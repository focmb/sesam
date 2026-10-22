from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from .. import passwords, syncing
from ..models import Config
from ..i18n import tr

HELP = (
    "Verbindungen, Gruppen und Identitäten werden in einem Git-Repository abgeglichen (eine Datei pro Eintrag). "
    "Passwörter werden nur <b>verschlüsselt</b> abgelegt. Das Sync-Passwort muss auf allen Geräten gleich sein und "
    "<b>kann nicht wiederhergestellt werden</b>. Die Anmeldung am Repository läuft über Git: mit SSH-Schlüssel, "
    "mit bereits gespeicherten Git-Zugangsdaten oder (bei https://-Adressen) mit Benutzername und Passwort bzw. Token, "
    "die Sesam im Schlüsselbund des Systems ablegt."
)


class SyncDialog(QDialog):
    def __init__(self, cfg: Config, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        s = cfg.sync
        self.setWindowTitle(tr("Synchronisation"))
        self.setMinimumWidth(520)

        self.enabled = QCheckBox(tr("Synchronisation aktivieren"))
        self.enabled.setChecked(s.enabled)
        self.url = QLineEdit(s.url)
        self.url.setPlaceholderText(tr("git@server:gruppe/sesam.git oder https://…"))
        self.branch = QLineEdit(s.branch or "main")
        self.user = QLineEdit(s.username)
        self.user.setPlaceholderText(tr("nur bei https:// nötig – leer: SSH-Schlüssel bzw. vorhandene Git-Zugangsdaten"))
        self.git_pw = QLineEdit()
        self.git_pw.setEchoMode(QLineEdit.EchoMode.Password)
        self.git_pw.setPlaceholderText(
            tr("gespeichert – leer lassen zum Beibehalten") if s.git_password_saved else tr("Passwort oder Zugriffstoken")
        )
        self.auto = QCheckBox(tr("Automatisch abgleichen (beim Start und nach jeder Änderung)"))
        self.auto.setChecked(s.auto)
        self.passwords = QCheckBox(tr("Passwörter verschlüsselt mitsynchronisieren"))
        self.passwords.setChecked(s.passwords)
        self.phrase = QLineEdit()
        self.phrase.setEchoMode(QLineEdit.EchoMode.Password)
        self.phrase.setPlaceholderText(tr("gespeichert – leer lassen zum Beibehalten") if s.passphrase_saved else tr("mind. 8 Zeichen"))
        self.phrase2 = QLineEdit()
        self.phrase2.setEchoMode(QLineEdit.EchoMode.Password)
        self.phrase2.setPlaceholderText(tr("zur Kontrolle wiederholen"))
        self.status = QLabel(tr("Letzter Abgleich: {last_sync}", last_sync=s.last_sync) if s.last_sync else tr("Noch nicht abgeglichen."))
        test = QPushButton(tr("Verbindung testen"))
        test.clicked.connect(self._test)
        self.test_result = QLabel()
        self.test_result.setWordWrap(True)

        form = QFormLayout()

        form.setVerticalSpacing(8)
        form.addRow(tr("Repository-URL"), self.url)
        form.addRow(tr("Branch"), self.branch)
        form.addRow(tr("Benutzername"), self.user)
        form.addRow(tr("Passwort / Token"), self.git_pw)
        form.addRow("", self.auto)
        form.addRow("", self.passwords)
        form.addRow(tr("Sync-Passwort"), self.phrase)
        form.addRow("", self.phrase2)
        row = QWidget()
        r = QHBoxLayout(row)
        r.setContentsMargins(0, 0, 0, 0)
        r.addWidget(test)
        r.addWidget(self.test_result, 1)
        form.addRow("", row)

        help_label = QLabel(tr(HELP))
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
        for w in (self.url, self.branch, self.user, self.git_pw, self.auto, self.passwords):
            w.setEnabled(on)
        pw = on and self.passwords.isChecked()
        self.phrase.setEnabled(pw)
        self.phrase2.setEnabled(pw)

    def _test(self):
        url = self.url.text().strip()
        if not url:
            self.test_result.setText(tr("Bitte zuerst eine URL eintragen."))
            return
        QGuiApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self.test_result.setText(syncing.check_remote(url, self._credentials()))
        except (syncing.SyncError, passwords.PasswordError) as exc:
            self.test_result.setText(str(exc))
        finally:
            QGuiApplication.restoreOverrideCursor()

    def _credentials(self) -> tuple[str, str] | None:
        """Eingetragene Zugangsdaten; ohne neues Passwort das gespeicherte (für „Verbindung testen“)."""
        user = self.user.text().strip()
        if not user:
            return None
        password = self.git_pw.text()
        if not password and self.cfg.sync.git_password_saved:
            password = passwords.get_password(syncing.GIT_PASSWORD_KEY) or ""
        return (user, password)

    def _accept(self):
        s = self.cfg.sync
        on = self.enabled.isChecked()
        new_phrase = self.phrase.text()
        if on:
            if not self.url.text().strip():
                QMessageBox.warning(self, tr("Synchronisation"), tr("Bitte eine Repository-URL eintragen."))
                return
            if self.git_pw.text() and not self.user.text().strip():
                QMessageBox.warning(self, tr("Synchronisation"), tr("Zum Passwort fehlt der Benutzername."))
                return
            if self.passwords.isChecked():
                if new_phrase:
                    if len(new_phrase) < 8:
                        QMessageBox.warning(self, tr("Synchronisation"), tr("Das Sync-Passwort braucht mindestens 8 Zeichen."))
                        return
                    if new_phrase != self.phrase2.text():
                        QMessageBox.warning(self, tr("Synchronisation"), tr("Die beiden Sync-Passwörter stimmen nicht überein."))
                        return
                elif not s.passphrase_saved:
                    QMessageBox.warning(self, tr("Synchronisation"), tr("Bitte ein Sync-Passwort für die Verschlüsselung festlegen."))
                    return
        if new_phrase and on and self.passwords.isChecked():
            try:
                passwords.set_password(syncing.PASSPHRASE_KEY, new_phrase)
                s.passphrase_saved = True
            except passwords.PasswordError as exc:
                QMessageBox.critical(self, tr("Sync-Passwort"), str(exc))
                return
        if on:
            user = self.user.text().strip()
            try:
                if user and self.git_pw.text():
                    passwords.set_password(syncing.GIT_PASSWORD_KEY, self.git_pw.text())
                    s.git_password_saved = True
                elif not user and s.git_password_saved:  # Benutzername entfernt: gespeichertes Passwort löschen
                    passwords.delete_password(syncing.GIT_PASSWORD_KEY)
                    s.git_password_saved = False
            except passwords.PasswordError as exc:
                QMessageBox.critical(self, tr("Repository-Passwort"), str(exc))
                return
            s.username = user
        s.enabled = on
        s.url = self.url.text().strip()
        s.branch = self.branch.text().strip() or "main"
        s.auto = self.auto.isChecked()
        s.passwords = self.passwords.isChecked()
        self.accept()
