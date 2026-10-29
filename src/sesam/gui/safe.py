"""Fremde Texte (Server, Sync-Repository) dürfen in der Oberfläche nie als Rich-Text/HTML gedeutet werden.

Qt erkennt bei Beschriftungen, Meldungsfenstern und Tooltips selbst, ob ein Text wie HTML aussieht („Container <img …>“).
Ein Server könnte dann Links oder Bilder in Sesams Fenster einblenden. Daher: Beschriftungen auf Klartext stellen,
Tooltips escapen und die Standard-Meldungsfenster auf Klartext umstellen."""
from __future__ import annotations

import html

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QMessageBox


def tip(text: str) -> str:
    """Tooltip-Text, der garantiert als Text (mit Zeilenumbrüchen) angezeigt wird."""
    return "<p style='white-space:pre'>" + html.escape(text, quote=False) + "</p>" if text else ""


def plain(label: QLabel) -> QLabel:
    label.setTextFormat(Qt.TextFormat.PlainText)
    return label


def _box(icon):
    def show(parent, title, text, buttons=QMessageBox.StandardButton.Ok, defaultButton=QMessageBox.StandardButton.NoButton):
        box = QMessageBox(icon, title, text, buttons, parent)
        box.setTextFormat(Qt.TextFormat.PlainText)
        if defaultButton != QMessageBox.StandardButton.NoButton:
            box.setDefaultButton(defaultButton)
        return QMessageBox.StandardButton(box.exec())
    return staticmethod(show)


def install_plain_message_boxes() -> None:
    """Ersetzt QMessageBox.critical/warning/information/question durch Varianten mit Klartext."""
    QMessageBox.critical = _box(QMessageBox.Icon.Critical)
    QMessageBox.warning = _box(QMessageBox.Icon.Warning)
    QMessageBox.information = _box(QMessageBox.Icon.Information)

    def question(parent, title, text, buttons=QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                 defaultButton=QMessageBox.StandardButton.NoButton):
        return _box(QMessageBox.Icon.Question).__func__(parent, title, text, buttons, defaultButton)
    QMessageBox.question = staticmethod(question)
