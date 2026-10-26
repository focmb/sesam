"""WireGuard-Status dieses Rechners: Ist ein Tunnel aktiv, und wie frisch ist sein letzter Handshake?

Ohne Administratorrechte liefert das System nur, *dass* ein Tunnel läuft (Linux: ``/sys/class/net``, Windows: Netzwerkadapter
„WireGuard Tunnel“). Endpunkt, Handshake und Datenmenge kommen von ``wg show … dump`` und stehen nur zur Verfügung, wenn
``wg`` vorhanden ist und der Aufruf erlaubt ist; sonst bleiben diese Angaben leer.

``detect()`` wirft nie eine Ausnahme und blockiert höchstens ein paar Sekunden; im Hauptfenster läuft es im Hintergrund.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from .runtime import IS_WINDOWS, clean_env

STALE_AFTER = 180  # Sekunden: so alt darf der letzte Handshake höchstens sein (WireGuard erneuert alle ~2 Minuten)
NEVER = float("inf")  # Handshake-Alter, wenn noch keiner stattgefunden hat
TIMEOUT = 4  # Sekunden pro Hilfsprogramm
SYSFS_NET = Path("/sys/class/net")
IFF_UP = 0x1
_WINDOWS_WG = (r"C:\Program Files\WireGuard\wg.exe",)

CONNECTED, STALE, OFF, UNKNOWN = "connected", "stale", "off", "unknown"


@dataclass
class Tunnel:
    name: str
    handshake_age: float | None = None  # Sekunden seit dem letzten Handshake; ``None`` = unbekannt, ``NEVER`` = noch keiner
    endpoint: str = ""
    addresses: list[str] = field(default_factory=list)
    allowed_ips: list[str] = field(default_factory=list)
    rx: int | None = None
    tx: int | None = None

    @property
    def stale(self) -> bool:
        return self.handshake_age is not None and self.handshake_age > STALE_AFTER

    @property
    def has_details(self) -> bool:
        return bool(self.endpoint or self.allowed_ips or self.addresses or self.rx is not None
                    or self.handshake_age is not None)


@dataclass
class Status:
    state: str = UNKNOWN
    tunnels: list[Tunnel] = field(default_factory=list)


def status_of(tunnels: list[Tunnel]) -> Status:
    """„veraltet“ nur, wenn bei *allen* Tunneln der Handshake zu alt ist."""
    if not tunnels:
        return Status(OFF, [])
    return Status(STALE if all(t.stale for t in tunnels) else CONNECTED, tunnels)


# --- Hilfsprogramme aufrufen ---
def _run(args: list[str]) -> str | None:
    """Standardausgabe eines Programms oder ``None``, wenn es fehlt, scheitert oder zu lange braucht."""
    kwargs = {}
    if IS_WINDOWS:
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW  # kein aufblitzendes Konsolenfenster
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=TIMEOUT, env=clean_env(),
                              stdin=subprocess.DEVNULL, **kwargs)
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout if proc.returncode == 0 else None


def _wg_executable() -> str | None:
    found = shutil.which("wg") or shutil.which("wg.exe")
    if found:
        return found
    return next((p for p in _WINDOWS_WG if IS_WINDOWS and os.path.exists(p)), None)


# --- Linux ---
def linux_interfaces(sysfs: Path = SYSFS_NET) -> list[str] | None:
    """Namen der aktiven WireGuard-Schnittstellen; ``None``, wenn sich das System nicht lesen lässt."""
    try:
        entries = sorted(sysfs.iterdir())
    except OSError:
        return None
    names = []
    for entry in entries:
        try:
            uevent = (entry / "uevent").read_text(encoding="utf-8", errors="replace").splitlines()
            flags = int((entry / "flags").read_text().strip(), 16)
        except (OSError, ValueError):
            continue
        if "DEVTYPE=wireguard" in uevent and flags & IFF_UP:
            names.append(entry.name)
    return names


def _linux_addresses(name: str) -> list[str]:
    out = _run(["ip", "-j", "addr", "show", "dev", name])
    if not out:
        return []
    try:
        data = json.loads(out)
    except ValueError:
        return []
    result = []
    for link in data if isinstance(data, list) else []:
        for info in link.get("addr_info", []):
            if info.get("local"):
                result.append(f"{info['local']}/{info['prefixlen']}" if "prefixlen" in info else info["local"])
    return result


# --- Windows ---
def windows_adapters() -> list[str] | None:
    """Namen der aktiven WireGuard-Adapter (so heißt auch der Tunnel); ``None``, wenn PowerShell nicht antwortet."""
    command = ("Get-NetAdapter -ErrorAction SilentlyContinue | "
               "Where-Object { $_.InterfaceDescription -like 'WireGuard Tunnel*' -and $_.Status -eq 'Up' } | "
               "Select-Object Name | ConvertTo-Json -Compress")
    out = _run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command])
    if out is None:
        return None
    out = out.strip()
    if not out:
        return []
    try:
        data = json.loads(out)
    except ValueError:
        return None
    items = data if isinstance(data, list) else [data]
    return [str(i["Name"]) for i in items if isinstance(i, dict) and i.get("Name")]


# --- Einzelheiten von „wg show“ ---
def parse_dump(text: str, now: float | None = None) -> Tunnel | None:
    """Liest die Ausgabe von ``wg show <Schnittstelle> dump``.

    Die erste Zeile gehört der Schnittstelle (und enthält den privaten Schlüssel; sie wird nicht ausgewertet),
    jede weitere einem Gegenstelle: öffentlicher Schlüssel, PSK, Endpunkt, erlaubte IPs, Handshake (Unix-Zeit,
    0 = noch keiner), empfangene und gesendete Bytes, Keepalive.
    """
    now = time.time() if now is None else now
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return None
    tunnel = Tunnel("")
    ages: list[float] = []
    rx = tx = 0
    for line in lines[1:]:
        fields = line.split("\t")
        if len(fields) < 7:
            continue
        endpoint, allowed, handshake = fields[2], fields[3], fields[4]
        try:
            stamp, received, sent = int(handshake), int(fields[5]), int(fields[6])
        except ValueError:
            continue
        ages.append(NEVER if stamp == 0 else max(0.0, now - stamp))
        rx += received
        tx += sent
        if not tunnel.endpoint and endpoint != "(none)":
            tunnel.endpoint = endpoint
        for ip in allowed.split(","):
            ip = ip.strip()
            if ip and ip != "(none)" and ip not in tunnel.allowed_ips:
                tunnel.allowed_ips.append(ip)
    if not ages:
        return None
    tunnel.handshake_age = min(ages)  # die jüngste Gegenstelle zählt
    tunnel.rx, tunnel.tx = rx, tx
    return tunnel


def _details(name: str) -> Tunnel | None:
    wg = _wg_executable()
    if not wg:
        return None
    out = _run([wg, "show", name, "dump"])
    return parse_dump(out) if out else None


def _tunnel(name: str) -> Tunnel:
    tunnel = _details(name) or Tunnel(name)
    tunnel.name = name
    if not IS_WINDOWS:
        tunnel.addresses = _linux_addresses(name)
    return tunnel


def detect() -> Status:
    """Aktueller Zustand; ``UNKNOWN``, wenn er sich auf diesem System nicht ermitteln lässt."""
    try:
        if IS_WINDOWS:
            names = windows_adapters()
        elif sys.platform.startswith("linux"):
            names = linux_interfaces()
        else:
            names = None
        if names is None:
            return Status(UNKNOWN, [])
        return status_of([_tunnel(n) for n in names])
    except Exception:  # noqa: BLE001 – eine Statusanzeige darf das Programm nie stören
        return Status(UNKNOWN, [])
