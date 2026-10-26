"""WireGuard-Status dieses Rechners: Ist ein Tunnel aktiv, und wie frisch ist sein letzter Handshake?

Ohne Administratorrechte liefert das System nur, *dass* ein Tunnel läuft, seine Adresse und die übertragene Datenmenge
(Linux: ``/sys/class/net``, Windows: Netzwerkadapter „WireGuard Tunnel“). Endpunkt und Handshake kommen von ``wg show``
und stehen nur zur Verfügung, wenn der Aufruf erlaubt ist. Unter Linux kann dafür ein sudo-Passwort hinterlegt werden
(:class:`Elevation`); ohne Passwort, mit falschem Passwort oder ohne ``sudo`` bleibt es bei der einfachen Anzeige.

``detect()`` wirft nie eine Ausnahme und blockiert höchstens ein paar Sekunden; im Hauptfenster läuft es im Hintergrund.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field, replace
from pathlib import Path

from .runtime import IS_WINDOWS, clean_env

STALE_AFTER = 180  # Sekunden: so alt darf der letzte Handshake höchstens sein (WireGuard erneuert alle ~2 Minuten)
NEVER = float("inf")  # Handshake-Alter, wenn noch keiner stattgefunden hat
TIMEOUT = 4  # Sekunden pro Hilfsprogramm
DETAIL_TTL = 30  # Sekunden: so lange gelten die per sudo gelesenen Einzelheiten (jeder sudo-Aufruf steht im Systemprotokoll)
RETRY_AFTER = 60  # Sekunden Pause nach einem Fehler, der nicht am Passwort lag
SYSFS_NET = Path("/sys/class/net")
IFF_UP = 0x1
SUDO_KEY = "wireguard:sudo"  # Schlüssel im Schlüsselbund für das sudo-Passwort
_WINDOWS_WG = (r"C:\Program Files\WireGuard\wg.exe",)
_IFNAME = re.compile(r"[A-Za-z0-9_=+.][A-Za-z0-9_=+.-]{0,14}")  # Linux-Schnittstellennamen; nie mit „-“ am Anfang

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
    sudo_failed: bool = False  # das hinterlegte sudo-Passwort wurde nicht akzeptiert


def status_of(tunnels: list[Tunnel]) -> Status:
    """„veraltet“ nur, wenn bei *allen* Tunneln der Handshake zu alt ist."""
    if not tunnels:
        return Status(OFF, [])
    return Status(STALE if all(t.stale for t in tunnels) else CONNECTED, tunnels)


# --- Hilfsprogramme aufrufen ---
def _run_full(args: list[str], input: str | None = None) -> subprocess.CompletedProcess | None:
    """Ergebnis eines Programms oder ``None``, wenn es fehlt oder zu lange braucht. ``input`` geht über die Standardeingabe."""
    kwargs: dict = {"input": input} if input is not None else {"stdin": subprocess.DEVNULL}
    if IS_WINDOWS:
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW  # kein aufblitzendes Konsolenfenster
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=TIMEOUT, env=clean_env(), **kwargs)
    except (OSError, subprocess.SubprocessError):
        return None


def _run(args: list[str]) -> str | None:
    """Standardausgabe eines Programms oder ``None``, wenn es fehlt, scheitert oder zu lange braucht."""
    proc = _run_full(args)
    return proc.stdout if proc is not None and proc.returncode == 0 else None


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


def linux_traffic(name: str, sysfs: Path = SYSFS_NET) -> tuple[int, int] | None:
    """(empfangen, gesendet) in Bytes aus den Zählern der Schnittstelle; braucht keine Rechte."""
    try:
        stats = sysfs / name / "statistics"
        return int((stats / "rx_bytes").read_text()), int((stats / "tx_bytes").read_text())
    except (OSError, ValueError):
        return None


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
def _count(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def windows_tunnels() -> list[Tunnel] | None:
    """Aktive WireGuard-Adapter (so heißt auch der Tunnel) mit ihren Zählern; ``None``, wenn PowerShell nicht antwortet."""
    command = ("Get-NetAdapter -ErrorAction SilentlyContinue | "
               "Where-Object { $_.InterfaceDescription -like 'WireGuard Tunnel*' -and $_.Status -eq 'Up' } | "
               "ForEach-Object { $s = Get-NetAdapterStatistics -Name $_.Name -ErrorAction SilentlyContinue; "
               "[pscustomobject]@{ Name = $_.Name; Rx = $s.ReceivedBytes; Tx = $s.SentBytes } } | "
               "ConvertTo-Json -Compress")
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
    return [Tunnel(str(i["Name"]), rx=_count(i.get("Rx")), tx=_count(i.get("Tx")))
            for i in items if isinstance(i, dict) and i.get("Name")]


# --- Einzelheiten von „wg show“ ---
def parse_dump(text: str, now: float | None = None) -> Tunnel | None:
    """Liest die Ausgabe von ``wg show <Schnittstelle> dump``.

    Die erste Zeile gehört der Schnittstelle (und enthält den privaten Schlüssel; sie wird nicht ausgewertet),
    jede weitere einer Gegenstelle: öffentlicher Schlüssel, PSK, Endpunkt, erlaubte IPs, Handshake (Unix-Zeit,
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


def parse_show(text: str, now: float | None = None) -> Tunnel | None:
    """Liest vier mit ``---`` getrennte Abschnitte: ``endpoints``, ``latest-handshakes``, ``transfer``, ``allowed-ips``.

    Diese Abfragen enthalten im Gegensatz zu ``dump`` keinen privaten Schlüssel.
    """
    now = time.time() if now is None else now
    parts = re.split(r"(?m)^---\n?", text)
    if len(parts) != 4:
        return None
    endpoints, handshakes, transfer, allowed = ([f.split("\t") for f in part.splitlines() if f.strip()] for part in parts)
    ages: list[float] = []
    for fields in handshakes:
        try:
            stamp = int(fields[1])
        except (IndexError, ValueError):
            continue
        ages.append(NEVER if stamp == 0 else max(0.0, now - stamp))
    if not ages:
        return None  # keine Gegenstelle: nichts zu berichten
    tunnel = Tunnel("", handshake_age=min(ages))
    tunnel.endpoint = next((f[1] for f in endpoints if len(f) > 1 and f[1] != "(none)"), "")
    rx = tx = 0
    for fields in transfer:
        try:
            rx += int(fields[1])
            tx += int(fields[2])
        except (IndexError, ValueError):
            continue
    tunnel.rx, tunnel.tx = rx, tx
    for fields in allowed:
        for ip in (fields[1].split() if len(fields) > 1 else []):
            if ip != "(none)" and ip not in tunnel.allowed_ips:
                tunnel.allowed_ips.append(ip)
    return tunnel


def _details(name: str) -> Tunnel | None:
    """Einzelheiten direkt von ``wg`` (klappt nur mit den nötigen Rechten)."""
    wg = _wg_executable()
    if not wg:
        return None
    out = _run([wg, "show", name, "dump"])
    return parse_dump(out) if out else None


# --- sudo (nur Linux) ---
def sudo_available() -> bool:
    """Gibt es auf diesem System ein sinnvolles ``sudo`` (Linux, nicht schon als root)?"""
    if IS_WINDOWS or not sys.platform.startswith("linux") or shutil.which("sudo") is None:
        return False
    return not (hasattr(os, "geteuid") and os.geteuid() == 0)


def verify_sudo_password(password: str) -> bool:
    """Prüft das Passwort mit einem harmlosen Befehl (``true``), ohne einen zwischengespeicherten Nachweis zu nutzen."""
    if not password or not sudo_available():
        return False
    proc = _run_full(["sudo", "-S", "-k", "-p", "", "--", "true"], input=password + "\n")  # nie auf der Kommandozeile
    return proc is not None and proc.returncode == 0


class Elevation:
    """Optionales sudo-Passwort, mit dem ``wg show`` als root gefragt wird. Nur im Speicher; dauerhaft liegt es im Schlüsselbund.

    Nach einem abgelehnten Passwort wird sudo bis zum Neustart oder bis zu einem neuen Passwort nicht mehr gefragt
    (viele Systeme sperren die Anmeldung nach wenigen Fehlversuchen). Die Einzelheiten gelten ``DETAIL_TTL`` Sekunden,
    weil jeder sudo-Aufruf im Systemprotokoll steht.
    """

    def __init__(self, password: str = ""):
        self.password = password
        self.failed = False
        self._cache: dict[str, tuple[float, Tunnel]] = {}
        self._retry_at = 0.0

    @property
    def usable(self) -> bool:
        return bool(self.password) and not self.failed and sudo_available()

    def details(self, name: str, now: float | None = None) -> Tunnel | None:
        now = time.time() if now is None else now
        if not self.usable or not _IFNAME.fullmatch(name or "") or now < self._retry_at:
            return None
        cached = self._cache.get(name)
        if cached and now - cached[0] < DETAIL_TTL:
            tunnel = replace(cached[1])
            if tunnel.handshake_age is not None and tunnel.handshake_age != NEVER:
                tunnel.handshake_age += now - cached[0]  # seit dem Lesen ist Zeit vergangen
            return tunnel
        wg = _wg_executable()
        if not wg:
            return None
        script = "; echo ---; ".join(f"{shlex.quote(wg)} show {name} {what}"
                                     for what in ("endpoints", "latest-handshakes", "transfer", "allowed-ips"))
        proc = _run_full(["sudo", "-S", "-p", "", "--", "sh", "-c", script], input=self.password + "\n")
        tunnel = parse_show(proc.stdout, now) if proc is not None and proc.returncode == 0 else None
        if tunnel is None:
            if not verify_sudo_password(self.password):  # lag es am Passwort? Dann nicht weiter probieren
                self.failed = True
            else:
                self._retry_at = now + RETRY_AFTER
            return None
        tunnel.name = name
        self._cache[name] = (now, tunnel)
        return replace(tunnel)


def _fill(tunnel: Tunnel, elevation: Elevation | None):
    """Ergänzt, was sich ohne (und notfalls mit sudo) herausfinden lässt."""
    if not IS_WINDOWS:
        tunnel.addresses = _linux_addresses(tunnel.name)
        traffic = linux_traffic(tunnel.name)
        if traffic:
            tunnel.rx, tunnel.tx = traffic
    details = _details(tunnel.name)
    if details is None and elevation is not None:
        details = elevation.details(tunnel.name)
    if details is not None:
        tunnel.endpoint, tunnel.allowed_ips, tunnel.handshake_age = details.endpoint, details.allowed_ips, details.handshake_age
        if details.rx is not None and details.tx is not None:
            tunnel.rx, tunnel.tx = details.rx, details.tx


def detect(elevation: Elevation | None = None) -> Status:
    """Aktueller Zustand; ``UNKNOWN``, wenn er sich auf diesem System nicht ermitteln lässt."""
    try:
        if IS_WINDOWS:
            tunnels = windows_tunnels()
        elif sys.platform.startswith("linux"):
            names = linux_interfaces()
            tunnels = None if names is None else [Tunnel(n) for n in names]
        else:
            tunnels = None
        if tunnels is None:
            return Status(UNKNOWN, [])
        for tunnel in tunnels:
            _fill(tunnel, elevation)
        status = status_of(tunnels)
        status.sudo_failed = bool(elevation is not None and elevation.failed)
        return status
    except Exception:  # noqa: BLE001 – eine Statusanzeige darf das Programm nie stören
        return Status(UNKNOWN, [])
