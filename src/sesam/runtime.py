"""Hilfen für den Betrieb als gebündeltes Programm (PyInstaller) und als normales Python-Paket."""
from __future__ import annotations

import os
import sys
from pathlib import Path

IS_WINDOWS = sys.platform.startswith("win")
CLI_NAME = "sesam-cli"  # Konsolenvariante (Windows), damit der ssh-Wrapper ein Terminal hat


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def console_executable() -> str:
    """Programm, das für Konsolenaufgaben (ssh-Wrapper, askpass) gestartet wird."""
    exe = sys.executable
    if is_frozen():
        sibling = Path(exe).with_name(CLI_NAME + (".exe" if IS_WINDOWS else ""))
        return str(sibling) if sibling.exists() else exe
    if IS_WINDOWS and exe.lower().endswith("pythonw.exe"):
        console = exe[: -len("pythonw.exe")] + "python.exe"
        return console if os.path.exists(console) else exe
    return exe


def self_command() -> list[str]:
    """Befehl, mit dem sich Sesam selbst startet (ohne weitere Argumente)."""
    exe = console_executable()
    return [exe] if is_frozen() else [exe, "-m", "sesam"]


def clean_env(env: dict[str, str] | None = None) -> dict[str, str]:
    """Umgebung für Kindprozesse (ssh, git, Terminals).

    Das gebündelte Programm setzt LD_LIBRARY_PATH auf seine eigenen Bibliotheken; Systemprogramme würden damit
    abstürzen oder falsche Versionen laden. Außerdem soll ein erneut gestartetes Sesam als eigene Instanz laufen.
    """
    env = dict(os.environ if env is None else env)
    if is_frozen():
        orig = env.pop("LD_LIBRARY_PATH_ORIG", None)
        if orig is not None:
            env["LD_LIBRARY_PATH"] = orig
        else:
            env.pop("LD_LIBRARY_PATH", None)
        env.pop("LD_PRELOAD_ORIG", None)
        for key in [k for k in env if k.startswith("_PYI_")]:
            env.pop(key)
        env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return env
