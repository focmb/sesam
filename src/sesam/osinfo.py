"""Betriebssystem eines Hosts ermitteln (``/etc/os-release``, macOS, ``uname``, Windows ``ver``, Cisco IOS XE ``show version``).

Läuft über eine zweite SSH-Verbindung im Hintergrund (paramiko), nicht im Terminal des Anwenders. Das Ergebnis steht danach in
``Connection.os_info`` und wird mitgespeichert, damit es bei jedem Start sofort da ist.
"""
from __future__ import annotations

import re
import shlex
from pathlib import Path

from . import sftp
from .i18n import tr
from .models import Config, Connection

EXEC_TIMEOUT = 15  # Sekunden für die Abfrage selbst
MAX_LENGTH = 80

SCRIPT = (
    'if [ -r /etc/os-release ]; then . /etc/os-release; printf "%s\\n" "${PRETTY_NAME:-$NAME $VERSION_ID}"; '
    'elif [ "$(uname -s)" = Darwin ]; then printf "macOS %s\\n" "$(sw_vers -productVersion)"; '
    "else uname -sr; fi"
)
COMMAND = "sh -c " + shlex.quote(SCRIPT)  # unabhängig von der Login-Shell des Benutzers (fish, csh, …)
WINDOWS_COMMAND = "ver"  # OpenSSH unter Windows (cmd)
CISCO_COMMAND = "show version | include ^Cisco IOS XE"  # Cisco-Switches (IOS XE): "sh -c" ist dort ungültig

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_ERROR_WORDS = ("not found", "invalid", "unknown command", "not recognized", "nicht gefunden", "syntax error")


def parse(output: str) -> str:
    """Erste brauchbare Zeile der Ausgabe als kurzer Text; leer, wenn die Ausgabe nicht nach einem Betriebssystem aussieht."""
    for raw in (output or "").splitlines():
        line = " ".join(_CONTROL.sub(" ", raw).split())
        if not line:
            continue
        low = line.lower()
        if line.startswith("%") or any(word in low for word in _ERROR_WORDS):
            return ""  # Fehlermeldung eines Netzwerkgeräts oder einer fremden Shell
        windows = re.match(r"microsoft windows \[(?:version|versie)\s+([\d.]+)\]", low)
        if windows:
            return f"Windows {windows.group(1)}"
        return line[:MAX_LENGTH]
    return ""


def _exec(client, command: str) -> tuple[int, str]:
    _stdin, out, _err = client.exec_command(command, timeout=EXEC_TIMEOUT)
    text = out.read().decode("utf-8", "replace")
    return out.channel.recv_exit_status(), text


def detect(cfg: Config, conn: Connection, password: str | None = None, known_hosts: Path | None = None,
           timeout: float = 10.0, strict: bool = False) -> str:
    """Fragt den Host nach seinem Betriebssystem. Blockiert, gehört in einen Hintergrund-Thread.

    Liefert einen leeren Text, wenn der Host keine brauchbare Antwort gibt. ``strict``: unbekannte Host-Schlüssel nicht
    übernehmen (``sftp.SftpUnknownHostKey``), damit die Abfrage nie die Bestätigung im Terminal vorwegnimmt.
    Weitere Fehler: ``sftp.SftpAuthError``, ``sftp.SftpError``.
    """
    def connect():
        return sftp.open_client(cfg, conn, password=password, known_hosts=known_hosts, timeout=timeout,
                                purpose=tr("die Erkennung des Betriebssystems"), strict_host_keys=strict)[0]

    client = connect()
    found, last_error, answered = "", None, False
    try:
        for command in (COMMAND, WINDOWS_COMMAND, CISCO_COMMAND):
            try:
                status, text = _exec(client, command)
            except (OSError, EOFError, sftp._paramiko().SSHException) as exc:
                # Manche Netzwerkgeräte (Cisco) beenden die Sitzung bei einem unbekannten Befehl: neu verbinden, nächster Befehl
                last_error = exc
                client.close()
                client = connect()
                continue
            answered = True
            found = parse(text) if status == 0 else ""
            if found:
                break
    finally:
        client.close()
    if not found and not answered and last_error is not None:
        raise sftp.SftpError(tr("Abfrage des Betriebssystems fehlgeschlagen: {exc}", exc=last_error)) from last_error
    return found
