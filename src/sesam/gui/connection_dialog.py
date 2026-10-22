from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QCompleter, QDialog, QLabel, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout,
    QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from .. import colors
from .. import groups as grp
from .. import tags as tag_util
from ..models import Config, Connection
from .identities_dialog import IdentityEditDialog, save_identity
from .keys_dialog import fill_key_combo
from ..i18n import tr


def swatch(color: str, size: int = 14) -> QIcon:
    """Kleiner Farbpunkt für Auswahllisten und Menüs."""
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(color))
    painter.drawEllipse(1, 1, size - 2, size - 2)
    painter.end()
    return QIcon(pix)


class TagCompleter(QCompleter):
    """Vervollständigt den Tag hinter dem letzten Komma einer Kommaliste."""

    def splitPath(self, path: str) -> list[str]:
        return [path.rpartition(",")[2].strip()]

    def pathFromIndex(self, index) -> str:
        completion = super().pathFromIndex(index)
        head = self.widget().text().rpartition(",")[0].strip()
        return f"{head}, {completion}" if head else completion


class ConnectionDialog(QDialog):
    def __init__(self, cfg: Config, conn: Connection | None = None, parent=None, default_group: str = "", save_callback=None):
        super().__init__(parent)
        self.cfg = cfg
        self.save_callback = save_callback or (lambda: None)
        self.conn = conn or Connection(name="", host="")
        self.setWindowTitle(tr("Verbindung bearbeiten") if conn else tr("Neue Verbindung"))
        self.setMinimumWidth(480)

        self.name = QLineEdit(self.conn.name)
        self.host = QLineEdit(self.conn.host)
        self.user = QLineEdit(self.conn.user)
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(self.conn.port or 22)
        self.identity = QLineEdit(self.conn.identity_file)
        self.key_combo = QComboBox()
        fill_key_combo(self.key_combo, self.cfg, self.conn.key_id)
        self.key_combo.currentIndexChanged.connect(self._identity_changed)
        browse = QPushButton("…")
        self._browse_btn = browse
        browse.setFixedWidth(40)
        browse.clicked.connect(self._browse_key)
        identity_row = QWidget()
        row = QHBoxLayout(identity_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.identity)
        row.addWidget(browse)
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText(
            tr("gespeichert – leer lassen zum Beibehalten") if self.conn.use_password else tr("optional")
        )
        self.password.setToolTip(
            tr("Für SSH-Login und (optional) sudo. Wird im Schlüsselbund des Betriebssystems gespeichert, nicht in der Konfigurationsdatei.")
        )
        self.clear_password = QCheckBox(tr("Gespeichertes Passwort löschen"))
        self.clear_password.setVisible(self.conn.use_password)
        self.password_change: str | None = None  # None = unverändert, "" = löschen, sonst neues Passwort
        self.identity_combo = QComboBox()
        self.identity_info = QLabel()
        self.identity_info.setWordWrap(True)
        new_identity = QPushButton(tr("Neu …"))
        new_identity.clicked.connect(self._new_identity)
        identity_pick = QWidget()
        pick_row = QHBoxLayout(identity_pick)
        pick_row.setContentsMargins(0, 0, 0, 0)
        pick_row.addWidget(self.identity_combo, 1)
        pick_row.addWidget(new_identity)
        self._fill_identities(self.conn.identity_id)
        self.identity_combo.currentIndexChanged.connect(self._identity_changed)
        self.jump = QLineEdit(self.conn.jump_host)
        self.jump.setPlaceholderText(tr("z. B. user@bastion.example.org"))
        self.extra = QLineEdit(self.conn.extra_args)
        self.extra.setPlaceholderText(tr("z. B. -o ServerAliveInterval=30 -L 8080:localhost:80"))
        self.remote = QLineEdit(self.conn.remote_command)
        self.remote.setPlaceholderText(tr("optional, z. B. tmux attach"))
        self.sudo_mode = QComboBox()
        self.sudo_mode.addItem(tr("Aus"), "")
        self.sudo_mode.addItem(tr("sudo -i (Login-Shell des Zielbenutzers)"), "login")
        self.sudo_mode.addItem(tr("sudo -s (Shell, Umgebung bleibt)"), "shell")
        self.sudo_mode.setCurrentIndex(max(self.sudo_mode.findData(self.conn.sudo_mode), 0))
        self.sudo_user = QLineEdit(self.conn.sudo_user)
        self.sudo_user.setPlaceholderText(tr("root (Standard)"))
        self.sudo_pw = QCheckBox(tr("Gespeichertes Passwort auch für sudo verwenden"))
        self.sudo_pw.setChecked(self.conn.sudo_use_password)
        self.sudo_pw.setToolTip(
            tr("Das Passwort wird verschlüsselt als Umgebungsvariable (SendEnv) übertragen. "
            "Der Server muss LC_* per AcceptEnv zulassen (Debian/Ubuntu: Standard); sonst fragt sudo im Terminal.")
        )

        def _sudo_toggled(*_):
            on = bool(self.sudo_mode.currentData())
            self.sudo_user.setEnabled(on)
            self.sudo_pw.setEnabled(on)

        self.sudo_mode.currentIndexChanged.connect(_sudo_toggled)
        _sudo_toggled()
        self.sudo_mode.setToolTip(tr("Nach dem Login per sudo zu root (oder einem anderen Benutzer) wechseln. "
                                  "Ein sudo-Passwort wird im Terminal abgefragt."))
        self.docker = QCheckBox(tr("Docker-Container dieses Hosts im Verbindungsbaum anzeigen"))
        self.docker.setChecked(self.conn.docker)
        self.docker.setToolTip(tr("Beim Aufklappen wird der Host per SSH nach laufenden Containern gefragt (docker ps)."))
        self.docker_shell = QLineEdit(self.conn.docker_shell)
        self.docker_shell.setPlaceholderText(tr("automatisch (bash, sonst sh)"))
        self.docker_user = QLineEdit(self.conn.docker_user)
        self.docker_user.setPlaceholderText(tr("Standard des Containers"))

        def _docker_toggled(*_):
            self.docker_shell.setEnabled(self.docker.isChecked())
            self.docker_user.setEnabled(self.docker.isChecked())

        self.docker.toggled.connect(_docker_toggled)
        _docker_toggled()
        self.group = QComboBox()
        self.group.setEditable(True)
        self.group.addItems([""] + grp.all_groups(cfg))
        self.group.setCurrentText(self.conn.group if conn else default_group)
        self.group.setToolTip(tr("Untergruppen mit „/“ trennen, z. B. Kunde A/Produktion"))
        self.color = QComboBox()
        self.color.addItem(tr("Keine"), "")
        for key, (name, hex_) in colors.PALETTE.items():
            name = tr(name)
            self.color.addItem(swatch(hex_), name, key)
        self.color.setCurrentIndex(max(self.color.findData(self.conn.color), 0))
        self.color.setToolTip(tr("Färbt das Symbol der Verbindung im Baum."))
        self.tags = QLineEdit(tag_util.format(self.conn.tags))
        self.tags.setPlaceholderText(tr("mit Komma trennen, z. B. prod, kunde-a"))
        completer = TagCompleter(tag_util.all_tags(cfg), self.tags)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.tags.setCompleter(completer)
        self.tags.setToolTip(tr("Schlagwörter zum Suchen und Ordnen; die Suche findet auch Tags."))
        self.terminal = QComboBox()
        self.terminal.addItem(tr("Standardterminal"), "")
        for t in cfg.terminals:
            self.terminal.addItem(t.name, t.id)
        idx = self.terminal.findData(self.conn.terminal_id)
        self.terminal.setCurrentIndex(max(idx, 0))
        self.notes = QPlainTextEdit(self.conn.notes)
        self.notes.setFixedHeight(70)

        form = QFormLayout()

        form.setVerticalSpacing(8)
        form.addRow(tr("Name*"), self.name)
        form.addRow(tr("Host*"), self.host)
        form.addRow(tr("Identität"), identity_pick)
        form.addRow("", self.identity_info)
        form.addRow(tr("Benutzer"), self.user)
        form.addRow(tr("Port"), self.port)
        form.addRow(tr("Gespeicherter Schlüssel"), self.key_combo)
        form.addRow(tr("Schlüsseldatei"), identity_row)
        form.addRow(tr("Passwort"), self.password)
        form.addRow("", self.clear_password)
        form.addRow(tr("Jump-Host"), self.jump)
        form.addRow(tr("Zusätzliche Optionen"), self.extra)
        form.addRow(tr("Remote-Befehl"), self.remote)
        form.addRow(tr("Mit sudo"), self.sudo_mode)
        form.addRow(tr("sudo als Benutzer"), self.sudo_user)
        form.addRow("", self.sudo_pw)
        form.addRow(tr("Docker"), self.docker)
        form.addRow(tr("Shell im Container"), self.docker_shell)
        form.addRow(tr("Benutzer im Container"), self.docker_user)
        form.addRow(tr("Gruppe"), self.group)
        form.addRow(tr("Farbe"), self.color)
        form.addRow(tr("Tags"), self.tags)
        form.addRow(tr("Terminal"), self.terminal)
        form.addRow(tr("Notizen"), self.notes)

        self._identity_changed()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def _fill_identities(self, select: str = ""):
        self.identity_combo.blockSignals(True)
        self.identity_combo.clear()
        self.identity_combo.addItem(tr("Keine – Zugangsdaten direkt eintragen"), "")
        for ident in sorted(self.cfg.identities, key=lambda i: i.name.lower()):
            self.identity_combo.addItem(ident.name, ident.id)
        self.identity_combo.setCurrentIndex(max(self.identity_combo.findData(select), 0))
        self.identity_combo.blockSignals(False)

    def _identity_changed(self, *_):
        ident = self.cfg.identity_by_id(self.identity_combo.currentData() or "")
        own = ident is None
        for w in (self.user, self.key_combo, self.password, self.clear_password):
            w.setEnabled(own)
        for w in (self.identity, self._browse_btn):
            w.setEnabled(own and not self.key_combo.currentData())
        if ident is None:
            self.identity_info.setText("")
        else:
            self.identity_info.setText(
                tr("Benutzer: {user} · Schlüssel: {key} · Passwort: {password}", user=ident.user or '—', key=self._identity_key_label(ident),
                   password=tr('gespeichert') if ident.use_password else '—')
            )

    def _identity_key_label(self, ident) -> str:
        stored = self.cfg.ssh_key_by_id(ident.key_id) if ident.key_id else None
        return stored.name if stored else (ident.identity_file or "—")

    def _new_identity(self):
        dlg = IdentityEditDialog(parent=self, cfg=self.cfg)
        if dlg.exec():
            save_identity(self, dlg.ident, dlg.password_change)
            self.cfg.identities.append(dlg.ident)
            self.save_callback()
            self._fill_identities(dlg.ident.id)
            self._identity_changed()

    def _browse_key(self):
        path, _ = QFileDialog.getOpenFileName(self, tr("Schlüsseldatei wählen"))
        if path:
            self.identity.setText(path)

    def _accept(self):
        if not self.name.text().strip() or not self.host.text().strip():
            QMessageBox.warning(self, tr("Eingabe unvollständig"), tr("Name und Host sind Pflichtfelder."))
            return
        c = self.conn
        c.name = self.name.text().strip()
        c.host = self.host.text().strip()
        c.identity_id = self.identity_combo.currentData() or ""
        c.user = self.user.text().strip()
        c.port = self.port.value()
        c.key_id = self.key_combo.currentData() or ""
        c.identity_file = "" if c.key_id else self.identity.text().strip()
        c.jump_host = self.jump.text().strip()
        c.extra_args = self.extra.text().strip()
        c.remote_command = self.remote.text().strip()
        c.sudo_mode = self.sudo_mode.currentData() or ""
        c.sudo_user = self.sudo_user.text().strip() if c.sudo_mode else ""
        c.sudo_use_password = self.sudo_pw.isChecked()
        c.docker = self.docker.isChecked()
        c.docker_shell = self.docker_shell.text().strip()
        c.docker_user = self.docker_user.text().strip()
        c.group = grp.normalize(self.group.currentText())
        c.color = self.color.currentData() or ""
        c.tags = tag_util.parse(self.tags.text())
        c.terminal_id = self.terminal.currentData() or ""
        c.notes = self.notes.toPlainText().strip()
        if self.password.text():
            self.password_change = self.password.text()
        elif self.clear_password.isChecked():
            self.password_change = ""
        self.accept()

    def result_connection(self) -> Connection:
        return self.conn
