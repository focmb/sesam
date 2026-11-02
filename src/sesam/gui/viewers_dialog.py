from __future__ import annotations

import copy

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMenu, QPushButton, QVBoxLayout,
)

from .. import launcher
from ..i18n import tr
from ..models import Config, Viewer
from ..viewers import template_viewers

HELP = (
    "<b>Platzhalter:</b><br>"
    "<code>{host}</code> <code>{port}</code> <code>{display}</code> <code>{url}</code> <code>{title}</code> – Ziel der VNC-Verbindung "
    "(bei VNC über SSH der lokale Tunnelendpunkt)<br>"
    "<code>{profile}</code> – Remmina-Profildatei, wird von Sesam erzeugt (samt gespeichertem Passwort)<br>"
    "<code>{password_file}</code> – VNC: Passwortdatei im Format von <code>vncpasswd</code> (z. B. für <code>-passwd</code>); "
    "ohne gespeichertes Passwort entfällt das Argument<br>"
    "<code>{user}</code> <code>{domain}</code> <code>{rdp_file}</code> – RDP: Benutzer, Domäne (fehlen sie, entfällt das Argument) "
    "und .rdp-Datei (z. B. für <code>mstsc</code>); <code>/from-stdin</code> übergibt das Passwort an <code>xfreerdp</code> über die Standardeingabe"
)

PLATFORMS = [("Alle", "all"), ("Linux", "linux"), ("Windows", "windows")]


class ViewersDialog(QDialog):
    """Konfiguration der VNC-Viewer (Befehlsvorlagen)."""

    def __init__(self, cfg: Config, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Viewer konfigurieren"))
        self.resize(720, 440)
        self.viewers: list[Viewer] = copy.deepcopy(cfg.viewers)
        self.default_ids = {"vnc": cfg.default_viewer_id, "rdp": cfg.default_rdp_viewer_id}
        self.protocol = "vnc"
        self._current: Viewer | None = None
        self.protocol_box = QComboBox()
        self.protocol_box.addItem("VNC", "vnc")
        self.protocol_box.addItem("RDP", "rdp")
        self.protocol_box.currentIndexChanged.connect(self._protocol_changed)

        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._select)
        add = QPushButton(tr("Neu ▾"))
        add_menu = QMenu(add)
        add_menu.aboutToShow.connect(lambda: self._fill_add_menu(add_menu))
        add.setMenu(add_menu)
        remove = QPushButton(tr("Löschen"))
        remove.clicked.connect(self._remove)
        make_default = QPushButton(tr("Als Standard"))
        make_default.clicked.connect(self._make_default)
        left_buttons = QHBoxLayout()
        for b in (add, remove):
            left_buttons.addWidget(b)
        left = QVBoxLayout()
        left.addWidget(self.protocol_box)
        left.addWidget(self.list)
        left.addLayout(left_buttons)
        left.addWidget(make_default)

        self.name = QLineEdit()
        self.command = QLineEdit()
        self.command.setPlaceholderText(tr("z. B. remmina -c {profile}"))
        pick = QPushButton(tr("Programm wählen …"))
        pick.clicked.connect(self._pick_program)
        self.platform = QComboBox()
        for label, value in PLATFORMS:
            self.platform.addItem(tr(label), value)
        self.name.textEdited.connect(self._store)
        self.command.textEdited.connect(self._store)
        self.platform.currentIndexChanged.connect(self._store)

        form = QFormLayout()
        form.setVerticalSpacing(8)
        form.addRow(tr("Name"), self.name)
        form.addRow(tr("Befehlsvorlage"), self.command)
        form.addRow("", pick)
        form.addRow(tr("Plattform"), self.platform)
        help_label = QLabel(tr(HELP))
        help_label.setWordWrap(True)
        help_label.setTextFormat(Qt.TextFormat.RichText)
        right = QVBoxLayout()
        right.addLayout(form)
        right.addWidget(help_label)
        right.addStretch(1)

        body = QHBoxLayout()
        body.addLayout(left, 1)
        body.addLayout(right, 2)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)
        layout.addLayout(body)
        layout.addWidget(buttons)

        self._reload_list()
        if self._shown():
            self.list.setCurrentRow(0)
        else:
            self._select(-1)

    def _shown(self) -> list[Viewer]:
        """Die Viewer des gewählten Protokolls (die Liste zeigt nur diese)."""
        return [v for v in self.viewers if v.protocol == self.protocol]

    def _protocol_changed(self, *_):
        self.protocol = self.protocol_box.currentData()
        self._reload_list(keep_row=0)
        self._select(self.list.currentRow())

    def _fill_add_menu(self, menu: QMenu):
        menu.clear()
        menu.addAction(tr("Eigener Viewer (leer)")).triggered.connect(lambda: self._add())
        menu.addSeparator()
        for tpl in template_viewers(self.protocol):
            menu.addAction(tr("Vorlage: {name}", name=tpl.name)).triggered.connect(lambda _=False, t=tpl: self._add(t))

    def _label(self, v: Viewer) -> str:
        return tr("{name} (Standard)", name=v.name) if v.id == self.default_ids.get(v.protocol) else v.name

    def _reload_list(self, keep_row: int | None = None):
        row = self.list.currentRow() if keep_row is None else keep_row
        shown = self._shown()
        self.list.blockSignals(True)
        self.list.clear()
        for v in shown:
            self.list.addItem(QListWidgetItem(self._label(v)))
        self.list.blockSignals(False)
        if 0 <= row < len(shown):
            self.list.setCurrentRow(row)

    def _select(self, row: int):
        shown = self._shown()
        self._current = shown[row] if 0 <= row < len(shown) else None
        widgets = (self.name, self.command, self.platform)
        for w in widgets:
            w.setEnabled(self._current is not None)
            w.blockSignals(True)
        v = self._current or Viewer("", "")
        self.name.setText(v.name)
        self.command.setText(v.command)
        self.platform.setCurrentIndex(max(self.platform.findData(v.platform), 0))
        for w in widgets:
            w.blockSignals(False)

    def _store(self, *_):
        v = self._current
        if v is None:
            return
        v.name = self.name.text()
        v.command = self.command.text()
        v.platform = self.platform.currentData()
        item = self.list.currentItem()
        if item:
            item.setText(self._label(v))

    def _pick_program(self):
        if not self._current:
            return
        path, _ = QFileDialog.getOpenFileName(self, tr("Viewer-Programm wählen"))
        if not path:
            return
        quoted = f'"{path}"' if " " in path else path
        rest = launcher.split_command(self.command.text())[1:]
        self.command.setText(" ".join([quoted] + [launcher.join_command([r]) for r in rest]) if rest else f"{quoted} {{host}}::{{port}}")
        self._store()

    def _add(self, template: Viewer | None = None):
        v = template or Viewer(tr("Eigener Viewer"), "", protocol=self.protocol)
        self.viewers.append(v)
        if not self.default_ids.get(self.protocol):
            self.default_ids[self.protocol] = v.id
        self._reload_list(keep_row=len(self._shown()) - 1)

    def _remove(self):
        row = self.list.currentRow()
        shown = self._shown()
        if row < 0 or row >= len(shown):
            return
        removed = shown[row]
        self.viewers.remove(removed)
        if removed.id == self.default_ids.get(self.protocol):
            rest = self._shown()
            self.default_ids[self.protocol] = rest[0].id if rest else ""
        self._reload_list(keep_row=min(row, len(self._shown()) - 1))
        self._select(self.list.currentRow())

    def _make_default(self):
        if self._current:
            self.default_ids[self.protocol] = self._current.id
            self._reload_list()

    def result_values(self) -> tuple[list[Viewer], str, str]:
        """Viewer sowie Standard-Viewer für VNC und RDP (ein ungültiger Standard wird durch den ersten Viewer des Protokolls ersetzt)."""
        defaults = []
        for protocol in ("vnc", "rdp"):
            own = [v for v in self.viewers if v.protocol == protocol]
            current = self.default_ids.get(protocol)
            defaults.append(current if any(v.id == current for v in own) else (own[0].id if own else ""))
        return self.viewers, defaults[0], defaults[1]
