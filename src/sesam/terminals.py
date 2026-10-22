from __future__ import annotations

import shutil
import sys
from dataclasses import replace

from .i18n import tr
from .models import Terminal, new_id

# (Terminal, ausführbare Datei zur Erkennung)
_LINUX = [
    (Terminal("Ghostty", "ghostty --title={title} -e {ssh_args}", platform="linux"), "ghostty"),
    (Terminal("GNOME Terminal", "gnome-terminal --title {title} -- {ssh_args}", platform="linux"), "gnome-terminal"),
    (Terminal("Konsole (KDE)", "konsole -p tabtitle={title} -e {ssh_args}", platform="linux"), "konsole"),
    (Terminal("Xfce Terminal", "xfce4-terminal --title {title} -x {ssh_args}", platform="linux"), "xfce4-terminal"),
    (Terminal("Kitty", "kitty --title {title} {ssh_args}", platform="linux"), "kitty"),
    (Terminal("Alacritty", "alacritty -t {title} -e {ssh_args}", platform="linux"), "alacritty"),
    (Terminal("WezTerm", "wezterm start -- {ssh_args}", platform="linux"), "wezterm"),
    (Terminal("xterm", "xterm -T {title} -e {ssh_args}", platform="linux"), "xterm"),
]

_WINDOWS = [
    (Terminal("Windows Terminal", "wt.exe new-tab --title {title} {ssh_args}", platform="windows"), "wt.exe"),
    (Terminal("PowerShell", "powershell.exe -NoExit -Command {ssh_command}", platform="windows", new_console=True), "powershell.exe"),
    (Terminal("Eingabeaufforderung (cmd)", "cmd.exe /k {ssh_command}", platform="windows", new_console=True), "cmd.exe"),
    (Terminal("PuTTY", "putty.exe -ssh -P {port} {destination}", platform="windows"), "putty.exe"),
]


def current_platform() -> str:
    return "windows" if sys.platform.startswith("win") else "linux"


def default_terminals() -> list[Terminal]:
    """Liefert Standard-Terminals für die aktuelle Plattform.

    Bevorzugt die auf dem System gefundenen; wird keines gefunden,
    werden alle Vorlagen der Plattform angeboten (zum Anpassen).
    """
    candidates = _WINDOWS if current_platform() == "windows" else _LINUX
    found = [t for t, exe in candidates if shutil.which(exe)]
    chosen = found or [t for t, _ in candidates]
    return [replace(t, id=new_id(), name=tr(t.name)) for t in chosen]


def template_terminals() -> list[Terminal]:
    """Alle bekannten Vorlagen der aktuellen Plattform (auch nicht installierte)."""
    candidates = _WINDOWS if current_platform() == "windows" else _LINUX
    return [replace(t, id=new_id(), name=tr(t.name)) for t, _ in candidates]
