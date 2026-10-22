from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from .. import i18n
from ..i18n import tr
from . import theme


class SettingsDialog(QDialog):
    """Allgemeine Einstellungen: Schriftgröße (wirkt sofort) und Sprache (wirkt nach einem Neustart)."""

    def __init__(self, font_scale: int, language: str, set_font_scale, set_language, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Einstellungen"))
        self.setMinimumWidth(420)
        self._set_font_scale = set_font_scale
        self._set_language = set_language
        self._initial_language = i18n.normalize(language)

        self.size_box = QComboBox()
        steps = list(theme.SCALE_STEPS)
        if font_scale not in steps:  # ungewöhnlicher Wert aus der Konfiguration bleibt auswählbar
            steps = sorted([*steps, font_scale])
        for pct in steps:
            self.size_box.addItem(f"{pct} %", pct)
        self.size_box.setCurrentIndex(self.size_box.findData(font_scale))
        self.size_box.setToolTip(tr("Schriftgröße der ganzen Oberfläche (Strg + Plus/Minus, Strg + 0 setzt auf 100 % zurück)"))
        self.size_box.currentIndexChanged.connect(lambda *_: self._set_font_scale(self.size_box.currentData()))
        reset = QPushButton(tr("Zurücksetzen (100 %)"))
        reset.setAutoDefault(False)  # „Schließen“ bleibt die Standardschaltfläche (und nicht blau hervorgehoben)
        reset.clicked.connect(lambda: self.size_box.setCurrentIndex(self.size_box.findData(100)))
        size_row = QHBoxLayout()
        size_row.addWidget(self.size_box, 1)
        size_row.addWidget(reset, 0)

        self.language_box = QComboBox()
        self.language_box.addItem(tr("Systemvorgabe"), "system")
        for code, name in i18n.LANGUAGE_NAMES.items():
            self.language_box.addItem(name, code)
        self.language_box.setCurrentIndex(max(0, self.language_box.findData(self._initial_language)))
        self.language_box.currentIndexChanged.connect(self._language_changed)
        self.restart_hint = QLabel(tr("Die Sprache wird beim nächsten Start von Sesam übernommen."))
        self.restart_hint.setWordWrap(True)
        self.restart_hint.setObjectName("hint")
        self.restart_hint.setVisible(False)

        form = QFormLayout()
        form.setVerticalSpacing(10)
        form.addRow(tr("Schriftgröße"), size_row)
        form.addRow(tr("Sprache"), self.language_box)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close = buttons.button(QDialogButtonBox.StandardButton.Close)
        close.setText(tr("Schließen"))
        close.setDefault(True)
        buttons.rejected.connect(self.accept)
        buttons.accepted.connect(self.accept)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)
        layout.addLayout(form)
        layout.addWidget(self.restart_hint)
        layout.addWidget(buttons)

    def _language_changed(self, *_):
        code = self.language_box.currentData()
        self._set_language(code)
        self.restart_hint.setVisible(code != self._initial_language)
