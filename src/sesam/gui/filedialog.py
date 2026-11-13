"""Dateiauswahl, die unter Linux auch versteckte Ordner und Dateien (z. B. ``~/.ssh``) zeigt.

Der Qt-Dialog blendet versteckte Einträge standardmäßig aus und bietet kein Strg+H. Hier wird unter Linux der
Qt-eigene Dialog verwendet, versteckte Einträge sind sichtbar und lassen sich mit Strg+H aus- und einblenden.
"""
from __future__ import annotations

import os
import sys

from PySide6.QtCore import QDir
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QFileDialog, QWidget

_BASE = QDir.AllEntries | QDir.NoDotAndDotDot | QDir.AllDirs | QDir.System


def pick_file(parent: QWidget | None, title: str, start_dir: str = "") -> str:
    """Öffnet einen Dateidialog und gibt den gewählten Pfad zurück (leer bei Abbruch)."""
    if sys.platform.startswith("win"):
        path, _ = QFileDialog.getOpenFileName(parent, title, start_dir)
        return path
    dlg = QFileDialog(parent, title, start_dir or os.path.expanduser("~"))
    dlg.setFileMode(QFileDialog.ExistingFile)
    dlg.setOption(QFileDialog.DontUseNativeDialog, True)
    state = {"hidden": True}

    def apply() -> None:
        dlg.setFilter(_BASE | QDir.Hidden if state["hidden"] else _BASE)

    def toggle() -> None:
        state["hidden"] = not state["hidden"]
        apply()

    apply()
    QShortcut(QKeySequence("Ctrl+H"), dlg, activated=toggle)
    if dlg.exec() and dlg.selectedFiles():
        return dlg.selectedFiles()[0]
    return ""


def ssh_dir() -> str:
    """``~/.ssh`` als Startordner, falls vorhanden, sonst der Home-Ordner."""
    d = os.path.join(os.path.expanduser("~"), ".ssh")
    return d if os.path.isdir(d) else os.path.expanduser("~")
