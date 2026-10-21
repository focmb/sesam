"""Hintergrundarbeit für Fenster: eine blockierende Funktion in einem Daemon-Thread, Ergebnisse als Qt-Signale.

Daemon-Thread statt QThread: Ein hängender Verbindungsaufbau darf das Beenden von Sesam nie blockieren oder abstürzen lassen.
"""
from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Signal


class Job(QObject):
    """Führt ``fn(job)`` im Hintergrund aus. ``fn`` darf ``job.progress`` und ``job.cancel_event`` nutzen."""

    result = Signal(object)
    failed = Signal(object)  # die Ausnahme
    progress = Signal(int, int, str)
    finished = Signal()  # immer zuletzt, nach result bzw. failed

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn
        self.cancel_event = threading.Event()
        self._done = threading.Event()
        self._started = False

    def start(self):
        self._started = True
        threading.Thread(target=self._run, daemon=True).start()

    def _emit(self, signal, *args):
        try:
            signal.emit(*args)
        except RuntimeError:  # das Fenster (und damit dieses Objekt) ist inzwischen zerstört
            pass

    def _run(self):
        try:
            value = self._fn(self)
        except BaseException as exc:  # auch Abbruch und unerwartete Fehler dürfen den Thread nicht still beenden
            self._done.set()
            self._emit(self.failed, exc)
        else:
            self._done.set()
            self._emit(self.result, value)
        self._emit(self.finished)

    def wait(self, msecs: int | None = None) -> bool:
        """Wartet höchstens ``msecs`` Millisekunden auf das Ende; True, wenn die Arbeit beendet ist."""
        return self._done.wait(None if msecs is None else msecs / 1000)

    def isRunning(self) -> bool:
        return self._started and not self._done.is_set()
