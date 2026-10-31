"""Port-Weiterleitungen (SSH-Tunnel): ssh-Aufruf bauen, Tunnel als Hintergrundprozesse starten und überwachen.

Jede Weiterleitung läuft als eigener ``ssh -N``-Prozess. Zugangsdaten, Jump-Host und Schlüssel gelten wie beim Terminal
(``launcher.resolve``); ein gespeichertes Passwort wird über SSH_ASKPASS übergeben. Ohne Passwort läuft ssh im
``BatchMode``: Es fragt nie nach (es gibt kein Terminal), sondern scheitert mit einer lesbaren Meldung.
"""
from __future__ import annotations

import ipaddress
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, replace

from . import askpass, connect_cli, launcher, runtime
from .i18n import tr
from .models import Config, Connection, Forward

IS_WINDOWS = sys.platform.startswith("win")

STARTING, RUNNING, FAILED, STOPPED = "starting", "running", "failed", "stopped"
READY_MARKER = "Entering interactive session"  # ssh -v meldet das, sobald Anmeldung und Weiterleitung stehen
READY_FALLBACK = 8.0  # Sekunden: läuft der Prozess so lange, gilt der Tunnel auch ohne die Meldung als aktiv
STOP_TIMEOUT = 3.0
_LOG_LINES = 40

_UNSAFE = re.compile(r"[\x00-\x20\x7f]")


class TunnelError(launcher.LaunchError):
    """Ungültige Weiterleitung oder Tunnel nicht startbar (lesbare Meldung)."""


# ---------------------------------------------------------------- Adressen und Beschriftung

def is_loopback(host: str) -> bool:
    """Bindet die Adresse nur an den eigenen Rechner? Leer steht für Loopback."""
    host = host.strip()
    if host in ("", "localhost"):
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def _host_part(host: str, what: str) -> str:
    """Host für das ssh-Argument: IPv6-Adressen in eckigen Klammern; Optionen, Leer- und Steuerzeichen sind verboten."""
    host = host.strip()
    if not host or host.startswith("-") or _UNSAFE.search(host) or "/" in host or "," in host:
        raise TunnelError(tr("Ungültiger Wert für {what}: {value}", what=what, value=repr(host)))
    bare = host.strip("[]")
    if ":" in bare:
        try:
            ipaddress.IPv6Address(bare)
        except ValueError as exc:
            raise TunnelError(tr("Ungültiger Wert für {what}: {value}", what=what, value=repr(host))) from exc
        return f"[{bare}]"
    return host


def _port(value: int, what: str) -> str:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 65535:
        raise TunnelError(tr("Ungültiger Wert für {what}: {value}", what=what, value=repr(value)))
    return str(value)


def forward_spec(f: Forward) -> list[str]:
    """Die ssh-Option dieser Weiterleitung, z. B. ``["-L", "127.0.0.1:8080:localhost:80"]``. ``TunnelError`` bei ungültigen Werten."""
    if f.kind not in ("local", "remote", "dynamic"):
        raise TunnelError(tr("Unbekannte Art der Weiterleitung: {value}", value=repr(f.kind)))
    bind = f.listen_host.strip() or ("localhost" if f.kind == "remote" else "127.0.0.1")
    if bind == "*":  # alle Adressen (wie ssh)
        bind_part = "*"
    else:
        bind_part = _host_part(bind, tr("Adresse"))
    listen = f"{bind_part}:{_port(f.listen_port, tr('Port'))}"
    if f.kind == "dynamic":
        return ["-D", listen]
    dest = f"{_host_part(f.dest_host, tr('Ziel'))}:{_port(f.dest_port, tr('Zielport'))}"
    return ["-L" if f.kind == "local" else "-R", f"{listen}:{dest}"]


def describe(f: Forward) -> str:
    """Kurzbeschreibung für Listen und Menüs, z. B. ``127.0.0.1:8080 → db:5432``."""
    bind = f.listen_host.strip() or ("localhost" if f.kind == "remote" else "127.0.0.1")
    listen = f"{bind}:{f.listen_port}"
    if f.kind == "dynamic":
        return tr("SOCKS-Proxy {listen}", listen=listen)
    if f.kind == "remote":
        return tr("Server {listen} → {dest}", listen=listen, dest=f"{f.dest_host}:{f.dest_port}")
    return f"{listen} → {f.dest_host}:{f.dest_port}"


def label(f: Forward) -> str:
    return f"{f.name} – {describe(f)}" if f.name.strip() else describe(f)


# ---------------------------------------------------------------- ssh-Aufruf

def build_args(conn: Connection, forward: Forward, with_password: bool = False) -> list[str]:
    """ssh-Aufruf für genau eine Weiterleitung. ``conn`` ist die wirksame Verbindung (nach ``launcher.resolve``)."""
    base = replace(conn, remote_command="", sudo_mode="", sudo_user="")  # ein Tunnel führt nie einen Befehl aus
    args = launcher.build_ssh_args(base)
    i = args.index("--")
    extra = ["-N", "-T", "-v", "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=30", "-o", "ServerAliveCountMax=3"]
    extra += ["-o", "NumberOfPasswordPrompts=1"] if with_password else ["-o", "BatchMode=yes"]
    extra += forward_spec(forward)
    return args[:i] + extra + args[i:]


def prepare(conn: Connection, forward: Forward, password: str | None, askpass_script: str = ""):
    """Argumente und Umgebung des Tunnels (rein funktional, daher testbar)."""
    base = replace(conn, remote_command="", sudo_mode="", sudo_user="")
    _, env = connect_cli.prepare(base, password, askpass_script)  # Umgebung samt SSH_ASKPASS, wenn ein Passwort vorliegt
    return build_args(base, forward, with_password=password is not None), env


# ---------------------------------------------------------------- unbekannte Host-Schlüssel

@dataclass
class PendingKey:
    name: str  # Name in known_hosts
    host: str
    port: int
    lines: list[str]  # Schlüsselzeilen von ssh-keyscan
    shown: str  # Fingerabdrücke zum Anzeigen


def pending_host_keys(conn: Connection) -> list[PendingKey]:
    """Hosts (Ziel und Jump-Hosts), die noch nicht in known_hosts stehen, samt ihren Fingerabdrücken.

    ``TunnelError``, wenn der Schlüssel eines Hosts nicht abgerufen werden kann. Ein Tunnel läuft ohne Terminal und
    kann die Host-Key-Frage von ssh nicht beantworten; deshalb wird sie vorher in Sesam gestellt."""
    if "stricthostkeychecking" in conn.extra_args.lower():
        return []
    if not (shutil.which("ssh-keygen") and shutil.which("ssh-keyscan")):
        return []
    found: list[PendingKey] = []
    for host, port in connect_cli.host_key_targets(conn):
        name = connect_cli._kh_name(host, port)
        if not host or connect_cli._is_known(name):
            continue
        scan = subprocess.run(["ssh-keyscan", "-T", "10", "-p", str(port), "--", host], stdin=subprocess.DEVNULL,
                              capture_output=True, text=True)
        lines = [ln for ln in scan.stdout.splitlines() if ln.strip() and not ln.startswith("#")]
        if not lines:
            raise TunnelError(tr("Der Schlüssel von {host} konnte nicht abgerufen werden.", host=name))
        shown = subprocess.run(["ssh-keygen", "-lf", "-"], input="\n".join(lines) + "\n", capture_output=True, text=True).stdout
        found.append(PendingKey(name, host, port, lines, shown.rstrip()))
    return found


def trust(key: PendingKey) -> None:
    """Trägt den bestätigten Host-Schlüssel in known_hosts ein."""
    path = connect_cli.known_hosts_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        needs_nl = path.exists() and path.stat().st_size > 0 and not path.read_bytes().endswith(b"\n")
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(("\n" if needs_nl else "") + "\n".join(key.lines) + "\n")
    except OSError as exc:
        raise TunnelError(tr("known_hosts kann nicht geschrieben werden: {exc}", exc=exc)) from exc


# ---------------------------------------------------------------- laufende Tunnel

def readable_error(log: list[str], code: int | None) -> str:
    """Die für Anwender nützliche Fehlermeldung aus der ssh-Ausgabe (ohne die Debug-Zeilen von ``-v``)."""
    lines = [ln.strip() for ln in log if ln.strip() and not ln.startswith("debug")]
    lines = [ln for ln in lines if not ln.startswith(("OpenSSH_", "Authenticated to", "Transferred:", "Bytes per second"))]
    if lines:
        return " ".join(lines[-3:])[:400]
    return tr("ssh wurde beendet (Exit-Code {code}).", code=code)


class Tunnel:
    """Ein laufender ``ssh -N``-Prozess für eine Weiterleitung."""

    def __init__(self, conn_id: str, forward: Forward, args: list[str], env: dict[str, str]):
        self.conn_id, self.forward = conn_id, forward
        self.args, self.env = args, env
        self.proc: subprocess.Popen | None = None
        self.started = 0.0
        self.log: deque[str] = deque(maxlen=_LOG_LINES)
        self._ready = threading.Event()
        self._stopping = False
        self._announced = False  # Zustandswechsel wurde dem Fenster schon gemeldet

    @property
    def key(self) -> tuple[str, str]:
        return (self.conn_id, self.forward.id)

    def start(self) -> None:
        if shutil.which(self.args[0]) is None:
            raise TunnelError(tr("ssh wurde nicht gefunden."))
        kwargs: dict = {}
        if IS_WINDOWS:
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        else:
            kwargs["start_new_session"] = True
        try:
            self.proc = subprocess.Popen(self.args, env=self.env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                         stderr=subprocess.PIPE, **kwargs)
        except OSError as exc:
            raise TunnelError(tr("Tunnel konnte nicht gestartet werden: {exc}", exc=exc)) from exc
        self.started = time.monotonic()
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        stream = self.proc.stderr
        try:
            for raw in iter(stream.readline, b""):
                line = raw.decode("utf-8", "replace").rstrip()
                self.log.append(line)
                if READY_MARKER in line:
                    self._ready.set()
        except (OSError, ValueError):
            pass

    def exit_code(self) -> int | None:
        return None if self.proc is None else self.proc.poll()

    @property
    def state(self) -> str:
        if self.proc is None or self._stopping and self.exit_code() is not None:
            return STOPPED
        code = self.exit_code()
        if code is not None:
            return STOPPED if self._stopping else FAILED
        if self._ready.is_set() or time.monotonic() - self.started > READY_FALLBACK:
            return RUNNING
        return STARTING

    @property
    def error(self) -> str:
        return readable_error(list(self.log), self.exit_code()) if self.state == FAILED else ""

    def stop(self) -> None:
        self._stopping = True
        proc = self.proc
        if proc is None or proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(STOP_TIMEOUT)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(STOP_TIMEOUT)
            except subprocess.TimeoutExpired:
                pass


class Manager:
    """Alle Tunnel der laufenden Sesam-Sitzung, nach (Verbindungs-ID, Weiterleitungs-ID)."""

    def __init__(self):
        self._tunnels: dict[tuple[str, str], Tunnel] = {}

    def start(self, conn: Connection, forward: Forward, password: str | None) -> Tunnel:
        """Startet den Tunnel (``conn`` ist die wirksame Verbindung). Ein schon laufender Tunnel wird nicht doppelt gestartet."""
        key = (conn.id, forward.id)
        current = self._tunnels.get(key)
        if current is not None and current.state in (STARTING, RUNNING):
            return current
        script = askpass.askpass_path() if password is not None else ""
        args, env = prepare(conn, forward, password, script)
        tunnel = Tunnel(conn.id, forward, args, env)
        tunnel.start()
        self._tunnels[key] = tunnel
        return tunnel

    def get(self, conn_id: str, forward_id: str) -> Tunnel | None:
        return self._tunnels.get((conn_id, forward_id))

    def state(self, conn_id: str, forward_id: str) -> str:
        t = self.get(conn_id, forward_id)
        return t.state if t is not None else STOPPED

    def active(self, conn_id: str | None = None) -> list[Tunnel]:
        return [t for t in self._tunnels.values() if t.state in (STARTING, RUNNING) and (conn_id is None or t.conn_id == conn_id)]

    def stop(self, conn_id: str, forward_id: str) -> None:
        t = self._tunnels.pop((conn_id, forward_id), None)
        if t is not None:
            t.stop()

    def stop_connection(self, conn_id: str) -> None:
        for key in [k for k in self._tunnels if k[0] == conn_id]:
            self.stop(*key)

    def stop_all(self) -> None:
        for key in list(self._tunnels):
            self.stop(*key)

    def newly_failed(self) -> list[Tunnel]:
        """Tunnel, die seit dem letzten Aufruf von selbst beendet wurden (einmal gemeldet)."""
        out = []
        for t in self._tunnels.values():
            if t.state == FAILED and not t._announced:
                t._announced = True
                out.append(t)
        return out

    def forget_missing(self, cfg: Config) -> None:
        """Beendet Tunnel, deren Verbindung oder Weiterleitung nicht mehr existiert (gelöscht oder geändert)."""
        for key, t in list(self._tunnels.items()):
            conn = cfg.connection_by_id(key[0])
            f = next((f for f in conn.forwards if f.id == key[1]), None) if conn else None
            if f is None or f != t.forward:
                self.stop(*key)
