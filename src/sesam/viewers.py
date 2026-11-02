"""Standard-Vorlagen für VNC- und RDP-Viewer (wie ``terminals`` für Terminals)."""
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

_FREERDP_ARGS = "/v:{host}:{port} /u:{user} /d:{domain} /cert:tofu +clipboard /dynamic-resolution /from-stdin"
_LINUX_RDP = [
    (Viewer("Remmina", "remmina -c {profile}", platform="linux", protocol="rdp"), "remmina"),
    (Viewer("FreeRDP", "xfreerdp " + _FREERDP_ARGS, platform="linux", protocol="rdp"), "xfreerdp"),
    (Viewer("FreeRDP 3", "xfreerdp3 " + _FREERDP_ARGS, platform="linux", protocol="rdp"), "xfreerdp3"),
]

_WINDOWS_RDP = [
    (Viewer("Remotedesktopverbindung (mstsc)", "mstsc {rdp_file}", platform="windows", protocol="rdp"), "mstsc"),
]


def _candidates(protocol: str):
    windows = current_platform() == "windows"
    if protocol == "rdp":
        return _WINDOWS_RDP if windows else _LINUX_RDP
    return _WINDOWS if windows else _LINUX


def default_viewers(protocol: str = "vnc") -> list[Viewer]:
    """Standard-Viewer der aktuellen Plattform: bevorzugt die gefundenen, sonst alle Vorlagen (zum Anpassen)."""
    candidates = _candidates(protocol)
    found = [v for v, exe in candidates if shutil.which(exe)]
    chosen = found or [v for v, _ in candidates]
    return [replace(v, id=new_id(), name=tr(v.name)) for v in chosen]


def template_viewers(protocol: str = "vnc") -> list[Viewer]:
    """Alle bekannten Vorlagen der aktuellen Plattform (auch nicht installierte)."""
    return [replace(v, id=new_id(), name=tr(v.name)) for v, _ in _candidates(protocol)]
