"""Farbpalette für Verbindungen und Gruppen. Gespeichert wird nur der Schlüssel (z. B. „blue“), nicht der Farbwert."""
from __future__ import annotations

from .i18n import tr

# Schlüssel -> (Bezeichnung, Farbwert); die Reihenfolge ist die Anzeigereihenfolge
PALETTE: dict[str, tuple[str, str]] = {
    "blue": ("Blau", "#3b82f6"),
    "teal": ("Türkis", "#14b8a6"),
    "green": ("Grün", "#22c55e"),
    "orange": ("Orange", "#f59e0b"),
    "red": ("Rot", "#ef4444"),
    "pink": ("Rosa", "#ec4899"),
    "purple": ("Lila", "#a855f7"),
    "gray": ("Grau", "#94a3b8"),
}


def valid(key: str) -> bool:
    return key in PALETTE


def label(key: str) -> str:
    return tr(PALETTE[key][0]) if key in PALETTE else tr("Keine")


def hex_of(key: str) -> str | None:
    """Farbwert zum Schlüssel; None für „keine Farbe“ und unbekannte Schlüssel (z. B. von einer neueren Version)."""
    return PALETTE[key][1] if key in PALETTE else None
