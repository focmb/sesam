from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QCompleter, QDialog, QLabel, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout,
    QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QSpinBox, QTabWidget, QVBoxLayout, QWidget,
)

from .. import colors
from .. import groups as grp
from .. import tags as tag_util
from ..models import REMOTE_DEFAULT_PORTS, Config, Connection
from .forwards_dialog import ForwardsPage
from .identities_dialog import IdentityEditDialog, save_identity
from .keys_dialog import fill_key_combo
from ..i18n import tr
from .filedialog import pick_file, ssh_dir


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
        self.setMinimumWidth(700)

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
        self.proxmox = QCheckBox(tr("Proxmox-Gäste (VMs und LXC) dieses Hosts im Verbindungsbaum anzeigen"))
        self.proxmox.setChecked(self.conn.proxmox)
        self.proxmox.setToolTip(tr("Beim Aufklappen wird der Proxmox-Host per SSH nach seinen Gästen gefragt (pvesh). "
                                   "Dafür ist der Benutzer root nötig. Ersetzt die Docker-Anzeige."))
        self.proxmox_vm_user = QLineEdit(self.conn.proxmox_vm_user)
        self.proxmox_vm_user.setPlaceholderText(tr("root"))

        self.cisco = QCheckBox(tr("Ports dieses Cisco-Geräts (IOS, IOS-XE, NX-OS) im Verbindungsbaum anzeigen"))
        self.cisco.setChecked(self.conn.cisco)
        self.cisco.setToolTip(tr("Beim Aufklappen werden die Ports per SSH abgefragt (show interfaces status). Ports lassen sich ein-/ausschalten "
                                 "und beschriften. Der Benutzer braucht Privilegstufe 15. Ersetzt die Docker- und Proxmox-Anzeige."))

        boxes = (self.docker, self.proxmox, self.cisco)

        def _exclusive(box, checked):
            # Docker, Proxmox und Cisco teilen sich die Unterelemente im Baum: nur eines gleichzeitig
            if checked:
                for other in boxes:
                    if other is not box and other.isChecked():
                        other.setChecked(False)
            self.proxmox_vm_user.setEnabled(self.proxmox.isChecked())

        for box in boxes:
            box.toggled.connect(lambda checked, b=box: _exclusive(b, checked))
        self.proxmox_vm_user.setEnabled(self.proxmox.isChecked())
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

        self._remote_before = (self.conn.remote, self.conn.remote_use_password)  # für den Wechsel des Protokolls (siehe MainWindow)
        self.remote_protocol = QComboBox()
        self.remote_protocol.addItem(tr("Aus"), "")
        self.remote_protocol.addItem("VNC", "vnc")
        self.remote_protocol.addItem("RDP", "rdp")
        self.remote_protocol.setCurrentIndex(max(self.remote_protocol.findData(self.conn.remote), 0))
        self.remote_mode = QComboBox()
        self.remote_mode.addItem(tr("Über SSH-Tunnel (Port ist nur auf dem Server erreichbar)"), "tunnel")
        self.remote_mode.addItem(tr("Direkt (Gerät spricht nur VNC oder RDP)"), "direct")
        self.remote_mode.setCurrentIndex(max(self.remote_mode.findData(self.conn.remote_mode), 0))
        self.remote_host = QLineEdit(self.conn.remote_host)
        self.remote_host.setPlaceholderText(tr("localhost (Standard)"))
        self.remote_host.setToolTip(tr("Ziel vom Server aus gesehen, z. B. ein Gerät im Netz hinter dem Server."))
        self.remote_port = QSpinBox()
        self.remote_port.setRange(1, 65535)
        self._port_protocol = self.conn.remote or "vnc"  # aus = wie VNC: der Port folgt dem Standard des später gewählten Protokolls
        self.remote_port.setValue(self.conn.remote_port or REMOTE_DEFAULT_PORTS[self._port_protocol])
        self.remote_viewer = QComboBox()
        self.remote_user = QLineEdit(self.conn.remote_user)
        self.remote_domain = QLineEdit(self.conn.remote_domain)
        self.remote_args = QLineEdit(self.conn.remote_args)
        self.remote_args.setPlaceholderText(tr("z. B. /f +clipboard"))
        self.remote_args.setToolTip(tr("Zusätzliche Argumente für den RDP-Viewer, getrennt wie in einer Shell."))
        self.remote_password = QLineEdit()
        self.remote_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.remote_password.setPlaceholderText(
            tr("gespeichert – leer lassen zum Beibehalten") if self.conn.remote_use_password else tr("optional")
        )
        self.remote_password.setToolTip(
            tr("Wird im Schlüsselbund des Betriebssystems gespeichert, nicht in der Konfigurationsdatei, und nicht synchronisiert. "
               "VNC verwendet nur die ersten 8 Zeichen.")
        )
        self.remote_clear_password = QCheckBox(tr("Gespeichertes Passwort löschen"))
        self.remote_clear_password.setVisible(self.conn.remote_use_password)
        self.remote_password_change: str | None = None  # None = unverändert, "" = löschen, sonst neues Passwort

        def _fill_remote_viewers(selected: str = ""):
            protocol = self.remote_protocol.currentData()
            self.remote_viewer.blockSignals(True)
            self.remote_viewer.clear()
            self.remote_viewer.addItem(tr("Standard-Viewer"), "")
            for v in cfg.viewers:
                if v.protocol == protocol:
                    self.remote_viewer.addItem(v.name, v.id)
            self.remote_viewer.setCurrentIndex(max(self.remote_viewer.findData(selected), 0))
            self.remote_viewer.blockSignals(False)

        def _remote_toggled(*_):
            protocol = self.remote_protocol.currentData()
            if protocol and protocol != self._port_protocol:  # der Port folgt dem Standard des Protokolls, solange er nicht geändert wurde
                if self.remote_port.value() == REMOTE_DEFAULT_PORTS.get(self._port_protocol):
                    self.remote_port.setValue(REMOTE_DEFAULT_PORTS[protocol])
                self._port_protocol = protocol
            _fill_remote_viewers(self.remote_viewer.currentData() or "")
            on = bool(protocol)
            for w in (self.remote_mode, self.remote_port, self.remote_viewer, self.remote_password, self.remote_clear_password):
                w.setEnabled(on)
            self.remote_host.setEnabled(on and self.remote_mode.currentData() == "tunnel")
            for w in (self.remote_user, self.remote_domain, self.remote_args):
                w.setEnabled(protocol == "rdp")

        _fill_remote_viewers(self.conn.remote_viewer_id)
        self.remote_protocol.currentIndexChanged.connect(_remote_toggled)
        self.remote_mode.currentIndexChanged.connect(_remote_toggled)
        _remote_toggled()

        def make_form() -> QFormLayout:
            f = QFormLayout()
            f.setVerticalSpacing(8)
            return f

        def make_page(f: QFormLayout) -> QWidget:
            page = QWidget()
            page.setLayout(f)
            return page

        general = make_form()
        general.addRow(tr("Name*"), self.name)
        general.addRow(tr("Host*"), self.host)
        general.addRow(tr("Gruppe"), self.group)
        general.addRow(tr("Farbe"), self.color)
        general.addRow(tr("Tags"), self.tags)
        general.addRow(tr("Terminal"), self.terminal)
        general.addRow(tr("Notizen"), self.notes)

        access = make_form()
        access.addRow(tr("Identität"), identity_pick)
        access.addRow("", self.identity_info)
        access.addRow(tr("Benutzer"), self.user)
        access.addRow(tr("Port"), self.port)
        access.addRow(tr("Gespeicherter Schlüssel"), self.key_combo)
        access.addRow(tr("Schlüsseldatei"), identity_row)
        access.addRow(tr("Passwort"), self.password)
        access.addRow("", self.clear_password)

        advanced = make_form()
        advanced.addRow(tr("Jump-Host"), self.jump)
        advanced.addRow(tr("Zusätzliche Optionen"), self.extra)
        advanced.addRow(tr("Remote-Befehl"), self.remote)
        advanced.addRow(tr("Mit sudo"), self.sudo_mode)
        advanced.addRow(tr("sudo als Benutzer"), self.sudo_user)
        advanced.addRow("", self.sudo_pw)

        tree = make_form()
        tree.addRow(tr("Docker"), self.docker)
        tree.addRow(tr("Shell im Container"), self.docker_shell)
        tree.addRow(tr("Benutzer im Container"), self.docker_user)
        tree.addRow(tr("Proxmox"), self.proxmox)
        tree.addRow(tr("Benutzer in VMs"), self.proxmox_vm_user)
        tree.addRow(tr("Cisco"), self.cisco)

        remote_form = make_form()
        remote_form.addRow(tr("Fernzugriff"), self.remote_protocol)
        remote_form.addRow(tr("Art"), self.remote_mode)
        remote_form.addRow(tr("Ziel vom Server aus"), self.remote_host)
        remote_form.addRow(tr("Port"), self.remote_port)
        remote_form.addRow(tr("Viewer"), self.remote_viewer)
        remote_form.addRow(tr("Benutzer (RDP)"), self.remote_user)
        remote_form.addRow(tr("Domäne (RDP)"), self.remote_domain)
        remote_form.addRow(tr("Zusatzargumente (RDP)"), self.remote_args)
        remote_form.addRow(tr("Passwort"), self.remote_password)
        remote_form.addRow("", self.remote_clear_password)

        self.tabs = QTabWidget()
        self.tabs.addTab(make_page(general), tr("Allgemein"))
        self.tabs.addTab(make_page(access), tr("Zugang"))
        self.tabs.addTab(make_page(advanced), tr("Erweitert"))
        self.tabs.addTab(make_page(tree), tr("Baum-Erweiterungen"))
        self.tabs.addTab(make_page(remote_form), tr("Fernzugriff"))
        self.forwards_page = ForwardsPage(self.conn.forwards)
        self.tabs.addTab(self.forwards_page, tr("Weiterleitungen"))

        self._identity_changed()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)
        layout.addWidget(self.tabs)
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
        path = pick_file(self, tr("Schlüsseldatei wählen"), ssh_dir())
        if path:
            self.identity.setText(path)

    def _accept(self):
        if not self.name.text().strip() or not self.host.text().strip():
            self.tabs.setCurrentIndex(0)
            (self.name if not self.name.text().strip() else self.host).setFocus()
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
        c.proxmox = self.proxmox.isChecked()
        c.cisco = self.cisco.isChecked()
        c.proxmox_vm_user = self.proxmox_vm_user.text().strip()
        c.group = grp.normalize(self.group.currentText())
        c.color = self.color.currentData() or ""
        c.tags = tag_util.parse(self.tags.text())
        c.terminal_id = self.terminal.currentData() or ""
        c.notes = self.notes.toPlainText().strip()
        c.forwards = list(self.forwards_page.forwards)
        c.remote = self.remote_protocol.currentData() or ""
        c.remote_mode = self.remote_mode.currentData() or "tunnel"
        c.remote_host = self.remote_host.text().strip() if c.remote_mode == "tunnel" else ""
        port = self.remote_port.value()
        c.remote_port = 0 if port == REMOTE_DEFAULT_PORTS.get(c.remote) else port
        c.remote_viewer_id = self.remote_viewer.currentData() or ""
        rdp = c.remote == "rdp"
        c.remote_user = self.remote_user.text().strip() if rdp else ""
        c.remote_domain = self.remote_domain.text().strip() if rdp else ""
        c.remote_args = self.remote_args.text().strip() if rdp else ""
        if self.remote_password.text():
            self.remote_password_change = self.remote_password.text()
        elif self.remote_clear_password.isChecked():
            self.remote_password_change = ""
        if self.password.text():
            self.password_change = self.password.text()
        elif self.clear_password.isChecked():
            self.password_change = ""
        self.accept()

    @property
    def remote_before(self) -> tuple[str, bool]:
        """Protokoll und „Passwort gespeichert“ vor dem Bearbeiten."""
        return self._remote_before

    def result_connection(self) -> Connection:
        return self.conn
