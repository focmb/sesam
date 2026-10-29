"""SSH_ASKPASS-Hilfe: liefert das Passwort aus der Umgebung, aber nur auf Passwortabfragen."""
from __future__ import annotations

import os
import stat
import sys

from . import runtime, storage

ENV_VAR = "SSHV_PASSWORD"
HOST_VAR = "SSHV_HOST"  # Zielhost: nur dessen Passwortabfragen werden beantwortet (nicht die eines Jump-Hosts)


def answer(prompt: str, env: dict[str, str] | None = None) -> str | None:
    """Passwort für Passwortabfragen, sonst None (z. B. Host-Key- oder Passphrase-Fragen)."""
    env = os.environ if env is None else env
    p = prompt.lower()
    if "passphrase" in p or not ("password" in p or "passwort" in p):
        return None
    h = env.get(HOST_VAR, "").lower()  # „user@host's password:“ bzw. „(user@host) Password:“ – andere Hosts bekommen nichts
    if not h or (f"@{h}'s " not in p and f"@{h}) " not in p):
        return None
    return env.get(ENV_VAR)


def main(argv: list[str]) -> int:
    value = answer(argv[0] if argv else "")
    if value is None:
        return 1
    sys.stdout.write(value + "\n")
    return 0


def askpass_path() -> str:
    """Legt das SSH_ASKPASS-Skript an (falls nötig) und liefert dessen Pfad."""
    d = storage.config_dir()
    d.mkdir(parents=True, exist_ok=True)
    if sys.platform.startswith("win"):
        path = d / "askpass.cmd"
        line = "@" + " ".join(f'"{part}"' for part in runtime.self_command()) + " --askpass %*\r\n"
        path.write_text(line, encoding="oem", newline="")  # cmd liest die OEM-Codepage (Pfade mit Umlauten)
    else:
        path = d / "askpass.sh"
        path.write_text(
            "#!/bin/sh\n"
            'case "$1" in\n'
            "  *[Pp]assphrase*) exit 1 ;;\n"
            f'  *"@${HOST_VAR}\'s "*[Pp]ass*|*"@${HOST_VAR}) "*[Pp]ass*) printf \'%s\\n\' "${ENV_VAR}" ;;\n'
            "  *) exit 1 ;;\n"
            "esac\n",
            encoding="utf-8",
        )
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)
