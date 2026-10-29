"""Cisco-Netzwerkgeräte (IOS, IOS-XE, NX-OS): Ports per SSH auflisten und verwalten (ein-/ausschalten, Beschreibung, speichern)."""
from __future__ import annotations

import re
import socket
import time
from dataclasses import dataclass
from pathlib import Path

from . import sftp
from .models import Config, Connection
from .i18n import tr

STATUS_COMMAND = "show interfaces status"  # gleich auf IOS, IOS-XE und NX-OS (NX-OS kürzt „interfaces“ zu „interface“ selbst)
COMMAND_TIMEOUT = 20.0  # Sekunden je Befehl
SAVE_TIMEOUT = 90.0  # Speichern der Konfiguration dauert auf älteren Geräten spürbar
MAX_DESCRIPTION = 200
_PROMPT = re.compile(r"^[\w.\-/()~@: ]{1,80}[#>] ?$")  # z. B. „sw1#“, „sw1(config-if)#“, „sw1>“
_PORT_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9/.:-]{0,40}$")
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_ERROR_MARKERS = ("% invalid", "% incomplete", "% ambiguous", "% unknown", "% bad", "% error", "% access denied",
                  "% authorization failed", "invalid command", "invalid input")


class CiscoError(Exception):
    """Fehler mit einer für Anwender lesbaren Meldung."""


@dataclass(frozen=True)
class Port:
    name: str
    description: str = ""
    status: str = ""  # „connected“, „notconnect“, „disabled“ (administrativ aus), „err-disabled“, …
    vlan: str = ""
    duplex: str = ""
    speed: str = ""
    type: str = ""

    @property
    def up(self) -> bool:
        return self.status.lower() == "connected"

    @property
    def disabled(self) -> bool:
        """Administrativ abgeschaltet (``shutdown``). Fehlerzustände wie err-disabled zählen nicht dazu."""
        return self.status.lower() == "disabled"


def parse_status(output: str) -> list[Port]:
    """Liest die Tabelle von ``show interfaces status``; die Spalten ergeben sich aus der Kopfzeile."""
    lines = output.splitlines()
    header = next((i for i, line in enumerate(lines) if line.startswith("Port") and "Status" in line), None)
    if header is None:
        raise CiscoError(tr("Die Portliste des Geräts ist nicht lesbar (kein „show interfaces status“?)."))
    head = lines[header]
    names = ("Port", "Name", "Status", "Vlan", "Duplex", "Speed", "Type")
    starts = [head.find(n) for n in names]
    if starts[0] != 0 or starts[2] < 0:
        raise CiscoError(tr("Die Portliste des Geräts ist nicht lesbar (kein „show interfaces status“?)."))
    # fehlende Spalten (-1) fallen weg; die Grenzen sind die Anfänge der jeweils nächsten vorhandenen Spalte
    present = [(n, s) for n, s in zip(names, starts) if s >= 0]
    found = []
    for line in lines[header + 1:]:
        if not line.strip():
            continue
        cells = {}
        for k, (n, s) in enumerate(present):
            end = present[k + 1][1] if k + 1 < len(present) else len(line)
            cells[n] = line[s:end].strip() if s < len(line) else ""
        if not cells.get("Port") or " " in cells["Port"]:
            continue
        found.append(Port(cells["Port"], cells.get("Name", ""), cells.get("Status", ""), cells.get("Vlan", ""),
                          cells.get("Duplex", ""), cells.get("Speed", ""), cells.get("Type", "")))
    return found


def check_port_name(name: str) -> str:
    if not _PORT_NAME.fullmatch(name or ""):  # fullmatch: „$“ würde ein abschließendes Zeilenende durchlassen
        raise CiscoError(tr("Der Portname „{name}“ ist ungültig.", name=name))
    return name


def check_description(text: str) -> str:
    """Beschreibung für ``description``: einzeilig, ohne „?“ (löst in der Geräte-Shell die Hilfe aus)."""
    text = " ".join(text.split())
    if not text.isprintable():  # Steuerzeichen (Strg+Z, ESC …) würden von der Geräte-Shell ausgewertet
        raise CiscoError(tr("Die Beschreibung enthält Steuerzeichen."))
    if "?" in text:
        raise CiscoError(tr("Die Beschreibung darf kein „?“ enthalten."))
    if len(text) > MAX_DESCRIPTION:
        raise CiscoError(tr("Die Beschreibung ist zu lang (höchstens {n} Zeichen).", n=MAX_DESCRIPTION))
    return text


def find_error(output: str) -> str:
    """Erste Fehlerzeile der Geräteantwort („% Invalid input …“), sonst leer."""
    for line in output.splitlines():
        low = line.strip().lower()
        if (low.startswith("%") and any(m in low for m in _ERROR_MARKERS)) or "invalid input detected" in low:
            return line.strip()
    return ""


def config_commands(port: str, *, shutdown: bool | None = None, description: str | None = None) -> list[str]:
    """Befehlsfolge, die einen Port ändert (rein, damit testbar)."""
    cmds = ["configure terminal", f"interface {check_port_name(port)}"]
    if shutdown is not None:
        cmds.append("shutdown" if shutdown else "no shutdown")
    if description is not None:
        text = check_description(description)
        cmds.append(f"description {text}" if text else "no description")
    cmds.append("end")
    return cmds


class _Shell:
    """Interaktive Shell auf dem Gerät: Befehl senden, bis zum nächsten Prompt lesen."""

    def __init__(self, client):
        try:
            self.chan = client.invoke_shell(term="vt100", width=250, height=2000)
        except Exception as exc:
            raise CiscoError(tr("Auf dem Gerät lässt sich keine Shell öffnen: {exc}", exc=exc)) from exc
        self.buffer = ""
        banner = self._read(COMMAND_TIMEOUT)
        self.prompt = banner.splitlines()[-1].strip() if banner.strip() else ""
        if self.prompt.endswith(">"):
            raise CiscoError(tr("Das Gerät meldet nur die Benutzerebene (Prompt „{prompt}“). "
                                "Bitte einen Benutzer mit Privilegstufe 15 verwenden.", prompt=self.prompt))
        self.run("terminal length 0")

    def _read(self, timeout: float) -> str:
        """Liest, bis die letzte Zeile wie ein Prompt aussieht. Gibt alles bis dahin zurück."""
        deadline = time.monotonic() + timeout
        text = ""
        while True:
            last = _ANSI.sub("", text).replace("\r", "").split("\n")[-1]
            if _PROMPT.match(last.strip()):
                return _ANSI.sub("", text).replace("\r", "")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CiscoError(tr("Zeitüberschreitung: Das Gerät hat nicht rechtzeitig geantwortet."))
            self.chan.settimeout(min(remaining, 1.0))
            try:
                chunk = self.chan.recv(4096)
            except socket.timeout:
                continue
            except OSError as exc:
                raise CiscoError(tr("Die Verbindung zum Gerät wurde getrennt: {exc}", exc=exc)) from exc
            if not chunk:
                raise CiscoError(tr("Die Verbindung zum Gerät wurde vom Gerät beendet."))
            text += chunk.decode("utf-8", "replace")

    def run(self, command: str, timeout: float = COMMAND_TIMEOUT) -> str:
        """Führt einen Befehl aus und liefert seine Ausgabe ohne Echo und Prompt. Fehlermeldungen des Geräts werden zur Ausnahme."""
        self.chan.sendall((command + "\n").encode())
        raw = self._read(timeout).split("\n")
        body = raw[1:-1] if len(raw) >= 2 else []  # erste Zeile = Echo des Befehls, letzte = neuer Prompt
        self.prompt = raw[-1].strip()
        out = "\n".join(body)
        if error := find_error(out):
            raise CiscoError(tr("Das Gerät meldet: {error}", error=error))
        return out

    def leave_config(self):
        try:
            self.chan.sendall(b"end\n")
        except OSError:
            pass


def _session(cfg, conn, password, known_hosts, timeout, purpose):
    client, _eff = sftp.open_client(cfg, conn, password=password, known_hosts=known_hosts, timeout=timeout, purpose=purpose)
    try:
        return client, _Shell(client)
    except BaseException:
        client.close()
        raise


def list_ports(cfg: Config, conn: Connection, password: str | None = None, known_hosts: Path | None = None,
               timeout: float = 10.0) -> list[Port]:
    """Fragt das Gerät nach seinen Ports. Blockiert, gehört in einen Hintergrund-Thread.

    Wirft ``sftp.SftpAuthError`` bei abgelehnter Anmeldung, sonst ``sftp.SftpError`` oder ``CiscoError``.
    """
    client, shell = _session(cfg, conn, password, known_hosts, timeout, tr("die Port-Anzeige"))
    try:
        return parse_status(shell.run(STATUS_COMMAND))
    finally:
        client.close()


def _configure(cfg, conn, commands: list[str], password, known_hosts, timeout):
    client, shell = _session(cfg, conn, password, known_hosts, timeout, tr("die Port-Verwaltung"))
    try:
        try:
            for command in commands:
                shell.run(command)
        except CiscoError:
            shell.leave_config()
            raise
    finally:
        client.close()


def set_shutdown(cfg: Config, conn: Connection, port: str, shutdown: bool, password: str | None = None,
                 known_hosts: Path | None = None, timeout: float = 10.0) -> None:
    """Schaltet einen Port administrativ aus (``shutdown``) oder ein (``no shutdown``)."""
    _configure(cfg, conn, config_commands(port, shutdown=shutdown), password, known_hosts, timeout)


def set_description(cfg: Config, conn: Connection, port: str, description: str, password: str | None = None,
                    known_hosts: Path | None = None, timeout: float = 10.0) -> None:
    """Setzt die Beschreibung eines Ports (leer = entfernen)."""
    _configure(cfg, conn, config_commands(port, description=description), password, known_hosts, timeout)


def save_config(cfg: Config, conn: Connection, password: str | None = None, known_hosts: Path | None = None,
                timeout: float = 10.0) -> None:
    """Speichert die laufende Konfiguration (``write memory``, auf NX-OS ``copy running-config startup-config``)."""
    client, shell = _session(cfg, conn, password, known_hosts, timeout, tr("die Port-Verwaltung"))
    try:
        try:
            shell.run("write memory", SAVE_TIMEOUT)
        except CiscoError as exc:
            if "invalid" not in str(exc).lower():
                raise
            shell.run("copy running-config startup-config", SAVE_TIMEOUT)
    finally:
        client.close()
