"""Standard-Vorlagen für VNC-Viewer (wie ``terminals`` für Terminals)."""
from __future__ import annotations

import shutil
from dataclasses import replace

from .i18n import tr
from .models import Viewer, new_id
from .terminals import current_platform

# (Viewer, ausführbare Datei zur Erkennung)
_LINUX = [
    (Viewer("Remmina", "remmina -c {profile}", platform="linux"), "remmina"),
    (Viewer("TigerVNC", "vncviewer -passwd {password_file} {host}::{port}", platform="linux"), "vncviewer"),
    (Viewer("KRDC (KDE)", "krdc {url}", platform="linux"), "krdc"),
]

_WINDOWS = [
    (Viewer("TigerVNC", "vncviewer.exe -passwd {password_file} {host}::{port}", platform="windows"), "vncviewer.exe"),
    (Viewer("RealVNC Viewer", "vncviewer.exe {host}:{port}", platform="windows"), "vncviewer.exe"),
]


def default_viewers() -> list[Viewer]:
    """Standard-Viewer der aktuellen Plattform: bevorzugt die gefundenen, sonst alle Vorlagen (zum Anpassen)."""
    candidates = _WINDOWS if current_platform() == "windows" else _LINUX
    found = [v for v, exe in candidates if shutil.which(exe)]
    chosen = found or [v for v, _ in candidates]
    return [replace(v, id=new_id(), name=tr(v.name)) for v in chosen]


def template_viewers() -> list[Viewer]:
    """Alle bekannten Vorlagen der aktuellen Plattform (auch nicht installierte)."""
    candidates = _WINDOWS if current_platform() == "windows" else _LINUX
    return [replace(v, id=new_id(), name=tr(v.name)) for v, _ in candidates]
