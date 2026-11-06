"""Kleine Hilfe für die Fehlersuche: Markierungen in ``crash.log`` (siehe ``__main__._enable_crash_log``).

Nach einem harten Absturz zeigt die letzte Markierung, wie weit der Ablauf gekommen ist."""
from __future__ import annotations

_file = None


def set_file(file) -> None:
    global _file
    _file = file


def note(message: str) -> None:
    if _file is None:
        return
    try:
        _file.write(message + "\n")
        _file.flush()
    except (OSError, ValueError):
        pass
