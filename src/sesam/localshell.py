"""Lokale Shell für das integrierte Terminal: bestimmt Programm und Umgebung für ein Terminal ohne SSH-Verbindung."""
from __future__ import annotations

import os
import shutil

from . import runtime


def shell() -> list[str]:
    """Die Shell selbst: unter Linux ``$SHELL`` (sonst bash/sh), unter Windows PowerShell 7, PowerShell oder die Eingabeaufforderung."""
    if runtime.IS_WINDOWS:
        for name in ("pwsh.exe", "powershell.exe"):
            path = shutil.which(name)
            if path:
                return [path, "-NoLogo"]
        return [os.environ.get("COMSPEC") or "cmd.exe"]
    shell = os.environ.get("SHELL", "")
    if shell and os.path.isabs(shell) and os.access(shell, os.X_OK):
        return [shell]
    for name in ("bash", "sh"):
        path = shutil.which(name)
        if path:
            return [path]
    return ["/bin/sh"]



def command() -> list[str]:
    """Befehl für die Sitzung. Unter Linux startet die Shell im Home-Verzeichnis (Sesam wird oft aus einem beliebigen Ordner gestartet)."""
    args = shell()
    if runtime.IS_WINDOWS:
        return args
    return ["/bin/sh", "-c", 'cd "$HOME" 2>/dev/null; exec "$0"', *args]
