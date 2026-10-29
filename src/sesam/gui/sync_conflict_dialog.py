from __future__ import annotations

from PySide6.QtWidgets import (
    QButtonGroup, QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QLabel, QPushButton, QRadioButton, QScrollArea,
    QVBoxLayout, QWidget,
)

from ..i18n import tr
from . import safe
from ..syncing import LOCAL, REMOTE, Conflict

MAX_VALUE = 60


def kind_label(kind: str) -> str:
    return {"c": tr("Verbindung"), "i": tr("Identität"), "k": tr("Gespeicherter Schlüssel"), "s": tr("Snippet")}.get(kind, kind)


def _short(value: object) -> str:
    text = "—" if value in (None, "") else str(value).replace("\n", " ")
    return text if len(text) <= MAX_VALUE else text[: MAX_VALUE - 1] + "…"


def describe(conflict: Conflict) -> str:
    lines = []
    for field, lv, rv in conflict.differences():
        if field == "password":
            lines.append(tr("Passwort ist verschieden"))
        else:
            lines.append(tr("{field}: {local} (lokal) ↔ {remote} (Server)", field=field, local=_short(lv), remote=_short(rv)))
    return "\n".join(lines)


class SyncConflictDialog(QDialog):
    """Fragt je Konflikt, ob die lokale oder die Server-Version gelten soll."""

    def __init__(self, conflicts: list[Conflict], parent=None):
        super().__init__(parent)
        self.conflicts = conflicts
        self.setWindowTitle(tr("Synchronisation – Konflikte"))
        self.setMinimumWidth(560)
        self._groups: dict[str, QButtonGroup] = {}
        self._local: dict[str, QRadioButton] = {}
        self._remote: dict[str, QRadioButton] = {}

        intro = QLabel(tr("Diese Einträge wurden lokal und auf dem Server unterschiedlich geändert. Welche Version soll gelten? "
                          "Die lokale Version wird danach auf den Server hochgeladen, die Server-Version ersetzt die lokale."))
        intro.setWordWrap(True)

        inner = QWidget()
        box = QVBoxLayout(inner)
        box.setContentsMargins(0, 0, 0, 0)
        for c in conflicts:
            frame = QFrame()
            frame.setFrameShape(QFrame.Shape.StyledPanel)
            fl = QVBoxLayout(frame)
            title = QLabel(f"<b>{_escape(c.name)}</b> · {kind_label(c.kind)}")
            fl.addWidget(title)
            diff = safe.plain(QLabel(describe(c)))
            diff.setWordWrap(True)
            fl.addWidget(diff)
            local = QRadioButton(tr("Lokale Version behalten (wird auf den Server hochgeladen)"))
            remote = QRadioButton(tr("Server-Version übernehmen (ersetzt die lokale)"))
            local.setChecked(True)
            group = QButtonGroup(self)
            group.addButton(local)
            group.addButton(remote)
            fl.addWidget(local)
            fl.addWidget(remote)
            box.addWidget(frame)
            self._groups[c.token] = group
            self._local[c.token] = local
            self._remote[c.token] = remote
        box.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(inner)
        scroll.setMinimumHeight(min(40 + 120 * len(conflicts), 420))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)
        layout.addWidget(intro)
        if len(conflicts) > 1:
            row = QHBoxLayout()
            all_local = QPushButton(tr("Alle lokal"))
            all_remote = QPushButton(tr("Alle vom Server"))
            all_local.clicked.connect(lambda: self._set_all(LOCAL))
            all_remote.clicked.connect(lambda: self._set_all(REMOTE))
            row.addWidget(all_local)
            row.addWidget(all_remote)
            row.addStretch(1)
            layout.addLayout(row)
        layout.addWidget(scroll, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _set_all(self, choice: str) -> None:
        for token in self._groups:
            (self._remote if choice == REMOTE else self._local)[token].setChecked(True)

    def choices(self) -> dict[str, str]:
        return {t: (REMOTE if r.isChecked() else LOCAL) for t, r in self._remote.items()}


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
