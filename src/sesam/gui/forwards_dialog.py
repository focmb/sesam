"""Port-Weiterleitungen (SSH-Tunnel) einer Verbindung bearbeiten."""
from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from .. import tunnels
from ..i18n import tr
from ..models import Forward
from . import safe

FORWARD_ROLE = int(Qt.ItemDataRole.UserRole)


class ForwardEditDialog(QDialog):
    """Eine Weiterleitung anlegen oder bearbeiten."""

    def __init__(self, forward: Forward | None = None, parent=None):
        super().__init__(parent)
        self.forward = replace(forward) if forward is not None else Forward()
        self.setWindowTitle(tr("Weiterleitung bearbeiten") if forward is not None else tr("Neue Weiterleitung"))
        self.setMinimumWidth(460)

        self.kind = QComboBox()
        self.kind.addItem(tr("Lokal (-L): entfernten Dienst auf diesem Rechner erreichen"), "local")
        self.kind.addItem(tr("Remote (-R): diesen Rechner auf dem Server freigeben"), "remote")
        self.kind.addItem(tr("Dynamisch (-D): SOCKS-Proxy über den Server"), "dynamic")
        self.kind.setCurrentIndex(max(self.kind.findData(self.forward.kind), 0))
        self.name = QLineEdit(self.forward.name)
        self.name.setPlaceholderText(tr("z. B. Datenbank"))
        self.listen_host = QLineEdit(self.forward.listen_host)
        self.listen_host.setPlaceholderText(tr("leer = nur dieser Rechner (127.0.0.1)"))
        self.listen_host.setToolTip(tr("Adresse, an der der Tunnel lauscht. Leer oder 127.0.0.1: nur dieser Rechner. "
                                       "0.0.0.0 oder * macht den Tunnel für alle Rechner im Netz erreichbar."))
        self.listen_port = QSpinBox()
        self.listen_port.setRange(1, 65535)
        self.listen_port.setValue(self.forward.listen_port or 8080)
        self.dest_host = QLineEdit(self.forward.dest_host)
        self.dest_host.setPlaceholderText(tr("Host, wie ihn der Server sieht, z. B. localhost"))
        self.dest_port = QSpinBox()
        self.dest_port.setRange(1, 65535)
        self.dest_port.setValue(self.forward.dest_port or 80)
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.hint.setObjectName("hint")

        self.listen_label = QLabel()
        self.dest_label = QLabel(tr("Ziel"))
        self.dest_port_label = QLabel(tr("Zielport"))
        form = QFormLayout()
        form.setVerticalSpacing(8)
        form.addRow(tr("Art"), self.kind)
        form.addRow(tr("Name"), self.name)
        form.addRow(tr("Lauschadresse"), self.listen_host)
        form.addRow(self.listen_label, self.listen_port)
        form.addRow(self.dest_label, self.dest_host)
        form.addRow(self.dest_port_label, self.dest_port)
        form.addRow("", self.hint)
        self.kind.currentIndexChanged.connect(self._kind_changed)
        self._kind_changed()

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def _kind_changed(self, *_):
        kind = self.kind.currentData()
        dynamic = kind == "dynamic"
        for w in (self.dest_label, self.dest_host, self.dest_port_label, self.dest_port):
            w.setVisible(not dynamic)
        self.listen_label.setText(tr("Port auf dem Server") if kind == "remote" else tr("Lokaler Port"))
        self.hint.setText({
            "local": tr("Verbindungen an den lokalen Port werden über den Server an das Ziel weitergeleitet, "
                        "z. B. localhost:5432 für eine Datenbank, die nur auf dem Server erreichbar ist."),
            "remote": tr("Verbindungen an den Port auf dem Server landen beim Ziel auf diesem Rechner, "
                         "z. B. localhost:3000 für einen lokalen Entwicklungsserver."),
            "dynamic": tr("Programme, die diesen SOCKS-Proxy nutzen, erreichen alles, was der Server erreicht."),
        }[kind])

    def _accept(self):
        f = self.forward
        f.kind = self.kind.currentData()
        f.name = self.name.text().strip()
        f.listen_host = self.listen_host.text().strip()
        f.listen_port = self.listen_port.value()
        f.dest_host = self.dest_host.text().strip() or "localhost"
        f.dest_port = self.dest_port.value()
        try:
            tunnels.forward_spec(f)
        except tunnels.TunnelError as exc:
            QMessageBox.warning(self, tr("Eingabe ungültig"), str(exc))
            return
        if not tunnels.is_loopback(f.listen_host) and f.kind != "remote":
            answer = QMessageBox.question(
                self, tr("Tunnel im Netz erreichbar"),
                tr("Die Adresse {host} macht den Tunnel für andere Rechner im Netz erreichbar – ohne Anmeldung. Trotzdem verwenden?", host=f.listen_host))
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.accept()

    def result_forward(self) -> Forward:
        return self.forward


class ForwardsPage(QWidget):
    """Liste der Weiterleitungen mit Knöpfen zum Hinzufügen, Bearbeiten und Entfernen (Arbeitskopie bis zum Bestätigen des Dialogs)."""

    def __init__(self, forwards: list[Forward], parent=None):
        super().__init__(parent)
        self.forwards: list[Forward] = [replace(f) for f in forwards]
        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(lambda *_: self._edit())
        self.add_button = QPushButton(tr("Hinzufügen …"))
        self.edit_button = QPushButton(tr("Bearbeiten …"))
        self.remove_button = QPushButton(tr("Entfernen"))
        for b in (self.add_button, self.edit_button, self.remove_button):
            b.setAutoDefault(False)
        self.add_button.clicked.connect(self._add)
        self.edit_button.clicked.connect(self._edit)
        self.remove_button.clicked.connect(self._remove)
        self.list.currentRowChanged.connect(self._update_buttons)
        hint = QLabel(tr("Gespeicherte Tunnel werden im Kontextmenü der Verbindung unter „Weiterleitungen“ gestartet und beendet."))
        hint.setWordWrap(True)
        hint.setObjectName("hint")
        buttons = QHBoxLayout()
        buttons.addWidget(self.add_button)
        buttons.addWidget(self.edit_button)
        buttons.addWidget(self.remove_button)
        buttons.addStretch(1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.addWidget(self.list, 1)
        layout.addLayout(buttons)
        layout.addWidget(hint)
        self._fill()

    def _fill(self, select: str = ""):
        self.list.clear()
        for f in self.forwards:
            item = QListWidgetItem(tunnels.label(f))
            item.setData(FORWARD_ROLE, f.id)
            item.setToolTip(safe.tip(tunnels.label(f)))
            self.list.addItem(item)
            if f.id == select:
                self.list.setCurrentItem(item)
        if self.list.currentRow() < 0 and self.list.count():
            self.list.setCurrentRow(0)
        self._update_buttons()

    def _update_buttons(self, *_):
        has = self.list.currentRow() >= 0
        self.edit_button.setEnabled(has)
        self.remove_button.setEnabled(has)

    def _current(self) -> Forward | None:
        item = self.list.currentItem()
        return next((f for f in self.forwards if item and f.id == item.data(FORWARD_ROLE)), None)

    def _add(self):
        dlg = ForwardEditDialog(parent=self)
        if dlg.exec():
            f = dlg.result_forward()
            self.forwards.append(f)
            self._fill(f.id)

    def _edit(self):
        current = self._current()
        if current is None:
            return
        dlg = ForwardEditDialog(current, self)
        if dlg.exec():
            new = dlg.result_forward()
            self.forwards[self.forwards.index(current)] = new
            self._fill(new.id)

    def _remove(self):
        current = self._current()
        if current is not None:
            self.forwards.remove(current)
            self._fill()
