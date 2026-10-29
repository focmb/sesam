"""Proxmox VE: Gäste (VMs und LXC-Container) eines Proxmox-Hosts per SSH auflisten, Shell öffnen, ein-/ausschalten."""
from __future__ import annotations

import ipaddress
import json
import re
import shlex
from dataclasses import dataclass, replace
from pathlib import Path

from . import sftp
from .models import Config, Connection
from .i18n import tr

# Erste Zeile: Name des Knotens, auf dem wir angemeldet sind; danach die Gäste des ganzen Clusters als JSON.
LIST_COMMAND = "hostname -s; pvesh get /cluster/resources --type vm --output-format json"
EXEC_TIMEOUT = 30  # Sekunden für die Abfrage selbst (der Verbindungsaufbau hat einen eigenen Timeout)
ACTIONS = ("start", "shutdown", "stop", "reboot")  # Strom-Aktionen (Reihenfolge wie im Menü)
_NODE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]*$")


class ProxmoxError(Exception):
    """Fehler mit einer für Anwender lesbaren Meldung."""


@dataclass(frozen=True)
class Guest:
    vmid: int
    name: str
    kind: str  # "qemu" (VM) oder "lxc" (Container)
    status: str  # "running", "stopped", …
    node: str = ""
    local: bool = True  # läuft auf dem Knoten, mit dem wir verbunden sind (sonst Umweg über den Knoten per SSH)

    @property
    def running(self) -> bool:
        return self.status == "running"

    @property
    def is_container(self) -> bool:
        return self.kind == "lxc"

    @property
    def title(self) -> str:
        return f"{self.name} ({self.vmid})"


def parse_resources(output: str) -> list[Guest]:
    """Liest die Ausgabe von ``LIST_COMMAND``. Vorlagen und unlesbare Einträge werden übergangen."""
    lines = output.strip().splitlines()
    if not lines:
        return []
    local_node = lines[0].strip()
    try:
        data = json.loads("\n".join(lines[1:]))
    except ValueError as exc:
        raise ProxmoxError(tr("Die Antwort von Proxmox ist nicht lesbar.")) from exc
    if not isinstance(data, list):
        raise ProxmoxError(tr("Die Antwort von Proxmox ist nicht lesbar."))
    found = []
    for item in data:
        if not isinstance(item, dict) or item.get("type") not in ("qemu", "lxc") or item.get("template"):
            continue
        try:
            vmid = int(item["vmid"])
        except (KeyError, TypeError, ValueError):
            continue
        node = str(item.get("node", ""))
        found.append(Guest(
            vmid=vmid, name=str(item.get("name") or vmid), kind=str(item["type"]),
            status=str(item.get("status", "")), node=node, local=(not node or node == local_node),
        ))
    return sorted(found, key=lambda g: g.vmid)


def describe_error(status: int, stderr: str) -> str:
    """Macht aus dem Fehler von ``pvesh`` eine verständliche Meldung."""
    text = stderr.strip()
    low = text.lower()
    if status == 127 or "command not found" in low or "pvesh: not found" in low:
        return tr("Der Host ist kein Proxmox-Server (Befehl „pvesh“ nicht gefunden).")
    if "permission denied" in low or "401" in low or "403" in low:
        return tr("Kein Zugriff auf Proxmox: Die Abfrage braucht den Benutzer root (oder entsprechende Rechte).")
    first = text.splitlines()[0] if text else tr("Fehlercode {status}", status=status)
    return tr("Proxmox meldet: {first}", first=first)


def _run(cfg: Config, conn: Connection, command: str, password: str | None, known_hosts: Path | None,
         timeout: float, purpose: str) -> str:
    client, _eff = sftp.open_client(cfg, conn, password=password, known_hosts=known_hosts, timeout=timeout, purpose=purpose)
    try:
        try:
            _stdin, out, err = client.exec_command(command, timeout=EXEC_TIMEOUT)
            stdout = out.read().decode("utf-8", "replace")
            stderr = err.read().decode("utf-8", "replace")
            status = out.channel.recv_exit_status()
        except (OSError, EOFError, sftp._paramiko().SSHException) as exc:
            raise ProxmoxError(tr("Abfrage von Proxmox fehlgeschlagen: {exc}", exc=exc)) from exc
    finally:
        client.close()
    if status != 0:
        raise ProxmoxError(describe_error(status, stderr))
    return stdout


def list_guests(cfg: Config, conn: Connection, password: str | None = None, known_hosts: Path | None = None,
                timeout: float = 10.0) -> list[Guest]:
    """Fragt den Proxmox-Host nach seinen Gästen. Blockiert, gehört in einen Hintergrund-Thread.

    Wirft ``sftp.SftpAuthError`` bei abgelehnter Anmeldung, sonst ``sftp.SftpError`` oder ``ProxmoxError``.
    """
    out = _run(cfg, conn, LIST_COMMAND, password, known_hosts, timeout, tr("die Proxmox-Anzeige"))
    return parse_resources(out)


def _api_path(guest: Guest, tail: str) -> str:
    if not _NODE.match(guest.node):
        raise ProxmoxError(tr("Der Knotenname „{node}“ ist ungültig.", node=guest.node))
    return f"/nodes/{guest.node}/{guest.kind}/{guest.vmid}/{tail}"


def power_command(guest: Guest, action: str) -> str:
    """Befehl für den Host, der den Gast ein-/ausschaltet (läuft über die API, daher auch für andere Knoten)."""
    if action not in ACTIONS:
        raise ProxmoxError(tr("Unbekannte Aktion „{action}“.", action=action))
    return shlex.join(["pvesh", "create", _api_path(guest, f"status/{action}")])


def power(cfg: Config, conn: Connection, guest: Guest, action: str, password: str | None = None,
          known_hosts: Path | None = None, timeout: float = 10.0) -> None:
    """Führt eine Strom-Aktion aus. Proxmox erledigt sie im Hintergrund; hier kommt nur die Annahme zurück."""
    _run(cfg, conn, power_command(guest, action), password, known_hosts, timeout, tr("die Proxmox-Steuerung"))


def pick_address(data) -> str:
    """Wählt aus der Antwort des Gast-Agenten (``network-get-interfaces``) die erste brauchbare IPv4-Adresse."""
    if isinstance(data, dict):
        data = data.get("result", data)
    if not isinstance(data, list):
        return ""
    fallback = ""
    for iface in data:
        if not isinstance(iface, dict) or str(iface.get("name", "")).startswith("lo"):
            continue
        for entry in iface.get("ip-addresses") or []:
            try:
                ip = ipaddress.ip_address(str(entry.get("ip-address", "")))
            except (ValueError, AttributeError):
                continue
            if ip.is_loopback or ip.is_link_local or ip.is_multicast:
                continue
            if ip.version == 4:
                return str(ip)
            fallback = fallback or str(ip)
    return fallback


def vm_address(cfg: Config, conn: Connection, guest: Guest, password: str | None = None,
               known_hosts: Path | None = None, timeout: float = 10.0) -> str:
    """Ermittelt die IP-Adresse einer VM über den QEMU-Gast-Agenten. Blockiert, gehört in einen Hintergrund-Thread."""
    command = shlex.join(["pvesh", "get", _api_path(guest, "agent/network-get-interfaces"), "--output-format", "json"])
    try:
        out = _run(cfg, conn, command, password, known_hosts, timeout, tr("die Proxmox-Anzeige"))
    except ProxmoxError as exc:
        raise ProxmoxError(tr("Die Adresse der VM „{name}“ ist nicht abfragbar (läuft der QEMU-Gast-Agent?): {exc}",
                              name=guest.title, exc=exc)) from exc
    try:
        address = pick_address(json.loads(out))
    except ValueError:
        address = ""
    if not address:
        raise ProxmoxError(tr("Die VM „{name}“ meldet keine IP-Adresse (läuft der QEMU-Gast-Agent?).", name=guest.title))
    return address


def shell_command(guest: Guest, local_node: bool | None = None) -> str:
    """Befehl für den Host, der eine Shell im LXC-Container öffnet (``pct enter``)."""
    if not guest.is_container:
        raise ProxmoxError(tr("Nur LXC-Container haben eine Shell über den Host; VMs werden per SSH erreicht."))
    enter = shlex.join(["pct", "enter", str(guest.vmid)])
    if guest.local if local_node is None else local_node:
        return enter
    if not _NODE.match(guest.node):
        raise ProxmoxError(tr("Der Knotenname „{node}“ ist ungültig.", node=guest.node))
    return shlex.join(["ssh", "-t", guest.node]) + " " + shlex.quote(enter)


def vm_command(address: str, user: str = "") -> str:
    """Befehl für den Proxmox-Host, der von dort per SSH in die VM springt."""
    target = f"{user.strip()}@{address}" if user.strip() else f"root@{address}"
    return shlex.join(["ssh", "-t", target])  # unbekannter Host: ssh fragt im Terminal mit Fingerabdruck nach


def guest_target(conn: Connection, guest: Guest, command: str) -> tuple[Connection, str]:
    """Verbindung (mit Gastnamen im Titel) und Befehl, um in den Gast zu gelangen."""
    return replace(conn, name=f"{conn.name} · {guest.name}"), command
