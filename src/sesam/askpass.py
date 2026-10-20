"""SSH_ASKPASS-Hilfe: liefert das Passwort aus der Umgebung, aber nur auf Passwortabfragen."""
from __future__ import annotations

import os
import stat
import sys

from . import storage

ENV_VAR = "SSHV_PASSWORD"


def answer(prompt: str, env: dict[str, str] | None = None) -> str | None:
    """Passwort für Passwortabfragen, sonst None (z. B. Host-Key- oder Passphrase-Fragen)."""
    env = os.environ if env is None else env
    p = prompt.lower()
    if "passphrase" in p or not ("password" in p or "passwort" in p):
        return None
    return env.get(ENV_VAR)


def main(argv: list[str]) -> int:
    value = answer(argv[0] if argv else "")
    if value is None:
        return 1
    sys.stdout.write(value + "\n")
    return 0


def _python_console() -> str:
    exe = sys.executable
    if sys.platform.startswith("win") and exe.lower().endswith("pythonw.exe"):
        candidate = exe[: -len("pythonw.exe")] + "python.exe"
        if os.path.exists(candidate):
            return candidate
    return exe


def askpass_path() -> str:
    """Legt das SSH_ASKPASS-Skript an (falls nötig) und liefert dessen Pfad."""
    d = storage.config_dir()
    d.mkdir(parents=True, exist_ok=True)
    if sys.platform.startswith("win"):
        path = d / "askpass.cmd"
        if getattr(sys, "frozen", False):
            line = f'@"{sys.executable}" --askpass %*\r\n'
        else:
            line = f'@"{_python_console()}" -m sesam --askpass %*\r\n'
        path.write_text(line, encoding="ascii", newline="")
    else:
        path = d / "askpass.sh"
        path.write_text(
            "#!/bin/sh\n"
            'case "$1" in\n'
            "  *[Pp]assphrase*) exit 1 ;;\n"
            f'  *[Pp]assword*|*[Pp]asswort*) printf \'%s\\n\' "${ENV_VAR}" ;;\n'
            "  *) exit 1 ;;\n"
            "esac\n",
            encoding="utf-8",
        )
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)
