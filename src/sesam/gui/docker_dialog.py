from __future__ import annotations

import html

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QVBoxLayout

from ..i18n import tr


class DockerConnectDialog(QDialog):
    """Fragt Shell und Benutzer für einen einzelnen Verbindungsaufbau in einen Container ab (wird nicht gespeichert)."""

    def __init__(self, container: str, shell: str = "", user: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Mit Container verbinden"))
        self.setMinimumWidth(420)
        self.shell = QLineEdit(shell)
        self.shell.setPlaceholderText(tr("automatisch (bash, sonst sh)"))
        self.shell.setToolTip(tr("Befehl, der im Container gestartet wird, z. B. /bin/zsh oder sh"))
        self.user = QLineEdit(user)
        self.user.setPlaceholderText(tr("Standard des Containers"))
        self.user.setToolTip(tr("Benutzer im Container, z. B. root oder www-data (docker exec -u)"))
        form = QFormLayout()
        form.setVerticalSpacing(8)
        form.addRow(tr("Shell"), self.shell)
        form.addRow(tr("Benutzer"), self.user)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(tr("Verbinden"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)
        layout.addWidget(QLabel(tr("Container <b>{container}</b>", container=html.escape(container))))
        layout.addLayout(form)
        layout.addWidget(buttons)

    def values(self) -> tuple[str, str]:
        return self.shell.text().strip(), self.user.text().strip()
