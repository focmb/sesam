from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout,
    QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from .. import groups as grp
from ..models import Config, Connection


class ConnectionDialog(QDialog):
    def __init__(self, cfg: Config, conn: Connection | None = None, parent=None, default_group: str = ""):
        super().__init__(parent)
        self.cfg = cfg
        self.conn = conn or Connection(name="", host="")
        self.setWindowTitle("Verbindung bearbeiten" if conn else "Neue Verbindung")
        self.setMinimumWidth(480)

        self.name = QLineEdit(self.conn.name)
        self.host = QLineEdit(self.conn.host)
        self.user = QLineEdit(self.conn.user)
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(self.conn.port or 22)
        self.identity = QLineEdit(self.conn.identity_file)
        browse = QPushButton("…")
        browse.setFixedWidth(32)
        browse.clicked.connect(self._browse_key)
        identity_row = QWidget()
        row = QHBoxLayout(identity_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.identity)
        row.addWidget(browse)
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText(
            "gespeichert – leer lassen zum Beibehalten" if self.conn.use_password else "optional"
        )
        self.password.setToolTip("Wird im Schlüsselbund des Betriebssystems gespeichert, nicht in der Konfigurationsdatei.")
        self.clear_password = QCheckBox("Gespeichertes Passwort löschen")
        self.clear_password.setVisible(self.conn.use_password)
        self.password_change: str | None = None  # None = unverändert, "" = löschen, sonst neues Passwort
        self.jump = QLineEdit(self.conn.jump_host)
        self.jump.setPlaceholderText("z. B. user@bastion.example.org")
        self.extra = QLineEdit(self.conn.extra_args)
        self.extra.setPlaceholderText("z. B. -o ServerAliveInterval=30 -L 8080:localhost:80")
        self.remote = QLineEdit(self.conn.remote_command)
        self.remote.setPlaceholderText("optional, z. B. tmux attach")
        self.sudo_mode = QComboBox()
        self.sudo_mode.addItem("Aus", "")
        self.sudo_mode.addItem("sudo -i (Login-Shell des Zielbenutzers)", "login")
        self.sudo_mode.addItem("sudo -s (Shell, Umgebung bleibt)", "shell")
        self.sudo_mode.setCurrentIndex(max(self.sudo_mode.findData(self.conn.sudo_mode), 0))
        self.sudo_user = QLineEdit(self.conn.sudo_user)
        self.sudo_user.setPlaceholderText("root (Standard)")
        self.sudo_mode.currentIndexChanged.connect(lambda *_: self.sudo_user.setEnabled(bool(self.sudo_mode.currentData())))
        self.sudo_user.setEnabled(bool(self.sudo_mode.currentData()))
        self.sudo_mode.setToolTip("Nach dem Login per sudo zu root (oder einem anderen Benutzer) wechseln. "
                                  "Ein sudo-Passwort wird im Terminal abgefragt.")
        self.group = QComboBox()
        self.group.setEditable(True)
        self.group.addItems([""] + grp.all_groups(cfg))
        self.group.setCurrentText(self.conn.group if conn else default_group)
        self.group.setToolTip("Untergruppen mit „/“ trennen, z. B. Kunde A/Produktion")
        self.terminal = QComboBox()
        self.terminal.addItem("Standardterminal", "")
        for t in cfg.terminals:
            self.terminal.addItem(t.name, t.id)
        idx = self.terminal.findData(self.conn.terminal_id)
        self.terminal.setCurrentIndex(max(idx, 0))
        self.notes = QPlainTextEdit(self.conn.notes)
        self.notes.setFixedHeight(70)

        form = QFormLayout()
        form.addRow("Name*", self.name)
        form.addRow("Host*", self.host)
        form.addRow("Benutzer", self.user)
        form.addRow("Port", self.port)
        form.addRow("Schlüsseldatei", identity_row)
        form.addRow("Passwort", self.password)
        form.addRow("", self.clear_password)
        form.addRow("Jump-Host", self.jump)
        form.addRow("Zusätzliche Optionen", self.extra)
        form.addRow("Remote-Befehl", self.remote)
        form.addRow("Mit sudo", self.sudo_mode)
        form.addRow("sudo als Benutzer", self.sudo_user)
        form.addRow("Gruppe", self.group)
        form.addRow("Terminal", self.terminal)
        form.addRow("Notizen", self.notes)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def _browse_key(self):
        path, _ = QFileDialog.getOpenFileName(self, "Schlüsseldatei wählen")
        if path:
            self.identity.setText(path)

    def _accept(self):
        if not self.name.text().strip() or not self.host.text().strip():
            QMessageBox.warning(self, "Eingabe unvollständig", "Name und Host sind Pflichtfelder.")
            return
        c = self.conn
        c.name = self.name.text().strip()
        c.host = self.host.text().strip()
        c.user = self.user.text().strip()
        c.port = self.port.value()
        c.identity_file = self.identity.text().strip()
        c.jump_host = self.jump.text().strip()
        c.extra_args = self.extra.text().strip()
        c.remote_command = self.remote.text().strip()
        c.sudo_mode = self.sudo_mode.currentData() or ""
        c.sudo_user = self.sudo_user.text().strip() if c.sudo_mode else ""
        c.group = grp.normalize(self.group.currentText())
        c.terminal_id = self.terminal.currentData() or ""
        c.notes = self.notes.toPlainText().strip()
        if self.password.text():
            self.password_change = self.password.text()
        elif self.clear_password.isChecked():
            self.password_change = ""
        self.accept()

    def result_connection(self) -> Connection:
        return self.conn
