"""Rückfrage zu unbekannten SSH-Host-Schlüsseln (paramiko-Verbindungen laufen in Hintergrund-Threads)."""
from __future__ import annotations

import threading

from PySide6.QtCore import QObject, QThread, Signal, Slot
from PySide6.QtWidgets import QApplication, QMessageBox

from ..i18n import tr

TIMEOUT = 120  # Sekunden, danach gilt die Frage als abgelehnt


class HostKeyAsker(QObject):
    _request = Signal(object)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._request.connect(self._ask)

    def confirm(self, host: str, key_type: str, fingerprint: str) -> bool:
        try:
            return self._confirm(host, key_type, fingerprint)
        except RuntimeError:  # Fenster samt Rückfrage-Objekt ist schon zerstört: im Zweifel nicht vertrauen
            return False

    def _confirm(self, host: str, key_type: str, fingerprint: str) -> bool:
        job = {"host": host, "type": key_type, "fp": fingerprint, "event": threading.Event(), "answer": False}
        if QThread.currentThread() is QApplication.instance().thread():
            self._ask(job)
        else:
            self._request.emit(job)
            job["event"].wait(TIMEOUT)
        return bool(job["answer"])

    @Slot(object)
    def _ask(self, job: dict) -> None:
        try:
            box = QMessageBox(self.parent() if isinstance(self.parent(), QObject) and self.parent().isWidgetType() else None)
            box.setIcon(QMessageBox.Icon.Question)
            box.setWindowTitle(tr("Unbekannter Host-Schlüssel"))
            box.setText(tr("Der Host {host} ist noch nicht bekannt.", host=job["host"]))
            box.setInformativeText(tr("Schlüsseltyp: {type}\nFingerabdruck: {fp}\n\nNur fortfahren, wenn der Fingerabdruck stimmt. "
                                      "Das Passwort wird sonst an einen Fremden gesendet.", type=job["type"], fp=job["fp"]))
            yes = box.addButton(tr("Vertrauen und verbinden"), QMessageBox.ButtonRole.AcceptRole)
            box.addButton(QMessageBox.StandardButton.Cancel)
            box.setDefaultButton(QMessageBox.StandardButton.Cancel)
            box.exec()
            job["answer"] = box.clickedButton() is yes
        finally:
            job["event"].set()
