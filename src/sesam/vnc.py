"""VNC: Viewer starten, bei Bedarf durch einen SSH-Tunnel.

Zwei Arten pro Verbindung (``Connection.vnc``):

* ``tunnel``: Sesam startet einen kurzlebigen ``ssh -N -L 127.0.0.1:<freier Port>:<Ziel>:<VNC-Port>`` (über den Tunnel-Manager,
  also mit Jump-Host, Schlüssel, Passwort und Host-Key-Rückfrage wie bei den Weiterleitungen) und öffnet den Viewer auf
  ``127.0.0.1:<freier Port>``. Endet der Viewer, wird der Tunnel wieder beendet.
* ``direct``: Der Viewer verbindet sich direkt mit ``host:vnc_port`` (Geräte, die nur VNC sprechen, ohne SSH).

Das VNC-Passwort liegt im Schlüsselbund (Schlüssel ``<Verbindungs-ID>:vnc``) und wird dem Viewer über eine Datei übergeben,
nie als Argument (sichtbar in der Prozessliste).
"""
from __future__ import annotations

import base64
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import launcher, passwords, runtime, tunnels
from .i18n import tr
from .models import Config, Connection, Forward, Viewer
from .terminals import current_platform

IS_WINDOWS = sys.platform.startswith("win")

START_TIMEOUT = 25.0  # Sekunden, bis der Tunnel stehen muss
DETACH_SECONDS = 4.0  # beendet sich der Viewer so schnell mit Code 0, hat er die Sitzung an eine laufende Instanz übergeben
PASSWORD_FILE_TTL = 30.0  # Sekunden, die eine Passwortdatei für den Viewer lesbar bleibt

STARTING, VIEWING, DETACHED, CLOSED = "starting", "viewing", "detached", "closed"
ACTIVE = (STARTING, VIEWING, DETACHED)


def password_key(conn_id: str) -> str:
    """Schlüssel des VNC-Passworts im Schlüsselbund (getrennt vom SSH-Passwort der Verbindung)."""
    return f"{conn_id}:vnc"


def apply_password_change(conn: Connection, change: str | None) -> None:
    """Wendet eine Änderung des VNC-Passworts an (None = unverändert, "" = löschen, sonst neues Passwort)."""
    if change is None:
        return
    if change:
        passwords.set_password(password_key(conn.id), change)
        conn.vnc_use_password = True
    else:
        passwords.delete_password(password_key(conn.id))
        conn.vnc_use_password = False


# ---------------------------------------------------------------- Ziel und Tunnel

def free_port() -> int:
    """Freier Port auf 127.0.0.1. Wird er bis zum Start von ssh belegt, scheitert ssh (ExitOnForwardFailure) mit lesbarer Meldung."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def tunnel_forward(conn: Connection, local_port: int) -> Forward:
    """Kurzlebige Weiterleitung für VNC (wird nicht in der Konfiguration gespeichert)."""
    return Forward(kind="local", listen_port=local_port, listen_host="127.0.0.1",
                   dest_host=conn.vnc_host.strip() or "localhost", dest_port=conn.vnc_port, name="VNC")


def title(conn: Connection) -> str:
    return conn.name or conn.host


# ---------------------------------------------------------------- Viewer wählen und Befehl bauen

def resolve_viewer(cfg: Config, conn: Connection) -> Viewer:
    viewer = cfg.viewer_by_id(conn.vnc_viewer_id) if conn.vnc_viewer_id else None
    viewer = viewer or cfg.viewer_by_id(cfg.default_viewer_id)
    if viewer is None:
        usable = [v for v in cfg.viewers if v.platform in ("all", current_platform())]
        viewer = usable[0] if usable else None
    if viewer is None:
        raise launcher.LaunchError(tr("Es ist kein VNC-Viewer konfiguriert. Bitte unter „VNC-Viewer“ einen anlegen."))
    return viewer


def uses_profile(viewer: Viewer) -> bool:
    return "{profile}" in viewer.command


def uses_password_file(viewer: Viewer) -> bool:
    return "{password_file}" in viewer.command


def viewer_command(viewer: Viewer, host: str, port: int, name: str, profile: str | None = None,
                   password_file: str | None = None) -> list[str]:
    """Befehl des Viewers als Argumentliste (nie über eine Shell).

    Ohne ``password_file`` entfallen Argumente mit ``{password_file}`` samt einer davorstehenden Option (``-passwd {password_file}``)."""
    ctx = {"{host}": host, "{port}": str(port), "{display}": str(port - 5900), "{url}": f"vnc://{host}:{port}", "{title}": name}
    if profile is not None:
        ctx["{profile}"] = profile
    if password_file is not None:
        ctx["{password_file}"] = password_file
    tokens = launcher.split_command(viewer.command)
    out: list[str] = []
    for i, token in enumerate(tokens):
        if "{password_file}" in token and password_file is None:
            # ohne Passwort entfällt das Argument samt seiner Option („-passwd {password_file}“)
            if token == "{password_file}" and out and i and tokens[i - 1] == out[-1] and out[-1].startswith("-") and "{" not in out[-1]:
                out.pop()
            continue
        for key, value in ctx.items():
            token = token.replace(key, value)
        out.append(token)
    if not out:
        raise launcher.LaunchError(tr("Die Befehlsvorlage von „{name}“ ist leer.", name=viewer.name))
    return out


def check_target(conn: Connection, host: str) -> None:
    """Host und Port dürfen nicht wie eine Option aussehen (der Wert landet als Argument im Viewer-Aufruf)."""
    launcher._check_target_part(host, tr("Host"))
    if not 1 <= conn.vnc_port <= 65535:
        raise launcher.LaunchError(tr("Ungültiger Wert für {what}: {value}", what=tr("Port"), value=repr(conn.vnc_port)))


# ---------------------------------------------------------------- Passwortübergabe

_VNC_DES_KEY = bytes([0xE8, 0x4A, 0xD6, 0x60, 0xC4, 0x72, 0x1A, 0xE0])  # festes Schlüssel von vncpasswd (bitweise gespiegelt)


def _des(key: bytes, mode):
    from cryptography.hazmat.primitives.ciphers import Cipher

    try:  # ab cryptography 43 liegt TripleDES unter „decrepit“
        from cryptography.hazmat.decrepit.ciphers.algorithms import TripleDES
    except ImportError:
        from cryptography.hazmat.primitives.ciphers.algorithms import TripleDES
    if len(key) == 8:
        key *= 3  # drei gleiche Teilschlüssel = einfaches DES, ohne Warnung zu 8-Byte-Schlüsseln
    return Cipher(TripleDES(key), mode)


def obfuscate_password(password: str) -> bytes:
    """Passwortdatei im Format von ``vncpasswd`` (8 Zeichen, mit dem festen VNC-DES-Schlüssel verschlüsselt)."""
    from cryptography.hazmat.primitives.ciphers import modes

    raw = password.encode("latin-1", "replace")[:8].ljust(8, b"\0")
    enc = _des(_VNC_DES_KEY, modes.ECB()).encryptor()
    return enc.update(raw) + enc.finalize()


def write_password_file(directory: str, password: str) -> str:
    path = os.path.join(directory, "passwd")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(obfuscate_password(password))
    return path


def remmina_pref_paths() -> list[Path]:
    home = Path.home()
    config = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
    return [config / "remmina" / "remmina.pref", home / ".var" / "app" / "org.remmina.Remmina" / "config" / "remmina" / "remmina.pref"]


def remmina_secret() -> bytes | None:
    """Schlüssel, mit dem Remmina gespeicherte Passwörter verschlüsselt (``secret=`` in remmina.pref, 32 Byte); None, wenn nicht gefunden."""
    for path in remmina_pref_paths():
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        m = re.search(r"^secret=(\S+)\s*$", text, re.MULTILINE)
        if not m:
            continue
        try:
            secret = base64.b64decode(m.group(1), validate=True)
        except ValueError:
            continue
        if len(secret) >= 32:
            return secret
    return None


def remmina_encrypt(password: str, secret: bytes) -> str:
    """Passwort im Remmina-Format: 3DES-CBC (Schlüssel = erste 24, IV = nächste 8 Byte von ``secret``), Nullbyte-Auffüllung, Base64."""
    from cryptography.hazmat.primitives.ciphers import modes

    raw = password.encode("utf-8")
    raw += b"\0" * (8 - len(raw) % 8)  # wie Remmina: immer mindestens ein Nullbyte
    enc = _des(secret[:24], modes.CBC(secret[24:32])).encryptor()
    return base64.b64encode(enc.update(raw) + enc.finalize()).decode("ascii")


def remmina_decrypt(value: str, secret: bytes) -> str:
    from cryptography.hazmat.primitives.ciphers import modes

    dec = _des(secret[:24], modes.CBC(secret[24:32])).decryptor()
    return (dec.update(base64.b64decode(value)) + dec.finalize()).rstrip(b"\0").decode("utf-8", "replace")


def _ini_value(text: str) -> str:
    return re.sub(r"[\x00-\x1f\x7f]", " ", text).strip()


def remmina_profile(name: str, host: str, port: int, password: str | None = None, secret: bytes | None = None) -> str:
    lines = ["[remmina]", f"name={_ini_value(name)}", "protocol=VNC", f"server={_ini_value(host)}:{port}"]
    if password and secret:
        lines.append(f"password={remmina_encrypt(password, secret)}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- Sitzungen

@dataclass
class Session:
    conn_id: str
    mode: str
    viewer: Viewer
    port: int = 0  # lokaler Tunnelport (nur "tunnel")
    forward: Forward | None = None
    state: str = STARTING
    started: float = field(default_factory=time.monotonic)
    viewer_started: float = 0.0
    proc: subprocess.Popen | None = None
    tmpdir: str | None = None
    password_file: str | None = None


Event = tuple[str, bool]  # (Text, Fehler?)


class Manager:
    """Alle VNC-Sitzungen dieser Sesam-Sitzung, je Verbindung höchstens eine."""

    def __init__(self, tunnel_manager: tunnels.Manager):
        self.tunnels = tunnel_manager
        self.sessions: dict[str, Session] = {}

    def is_open(self, conn_id: str) -> bool:
        s = self.sessions.get(conn_id)
        return s is not None and s.state in ACTIVE

    # --- Start ---
    def begin(self, cfg: Config, conn: Connection, start_tunnel) -> list[Event]:
        """Startet VNC für die Verbindung. ``start_tunnel(conn, forward)`` startet den kurzlebigen Tunnel (Rückfragen, Passwort) und liefert False bei Abbruch.

        Bei ``direct`` startet der Viewer sofort, bei ``tunnel`` erst, wenn ``poll`` den Tunnel als aktiv meldet."""
        if conn.vnc not in ("tunnel", "direct"):
            raise launcher.LaunchError(tr("Für diese Verbindung ist VNC nicht eingeschaltet."))
        if self.is_open(conn.id):
            return [(tr("VNC zu „{name}“ ist bereits geöffnet.", name=title(conn)), False)]
        viewer = resolve_viewer(cfg, conn)
        if conn.vnc == "direct":
            check_target(conn, conn.host)
            sess = Session(conn.id, "direct", viewer)
            events = self._launch(cfg, conn, sess)
            self.sessions[conn.id] = sess
            return events
        port = free_port()
        forward = tunnel_forward(conn, port)
        sess = Session(conn.id, "tunnel", viewer, port=port, forward=forward)
        if not start_tunnel(conn, forward):
            return []
        self.sessions[conn.id] = sess
        return [(tr("VNC-Tunnel wird gestartet: {name}", name=title(conn)), False)]

    def _launch(self, cfg: Config, conn: Connection, sess: Session) -> list[Event]:
        events: list[Event] = []
        host, port = ("127.0.0.1", sess.port) if sess.mode == "tunnel" else (conn.host, conn.vnc_port)
        password = None
        if conn.vnc_use_password:
            try:
                password = passwords.get_password(password_key(conn.id))
            except passwords.PasswordError as exc:
                events.append((tr("VNC-Passwort nicht gelesen: {exc}", exc=exc), True))
        viewer = sess.viewer
        profile = password_file = None
        try:
            if uses_profile(viewer) or (uses_password_file(viewer) and password):
                sess.tmpdir = tempfile.mkdtemp(prefix="sesam-vnc-")  # nur für den Benutzer lesbar (0700)
            if uses_profile(viewer):
                secret = remmina_secret() if password else None
                if password and secret is None:
                    events.append((tr("Das Passwort konnte Remmina nicht übergeben werden (Remmina-Schlüssel nicht gefunden). Remmina fragt danach."), True))
                profile = os.path.join(sess.tmpdir, "sesam.remmina")
                fd = os.open(profile, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(remmina_profile(title(conn), host, port, password, secret))
            if uses_password_file(viewer) and password:
                password_file = sess.password_file = write_password_file(sess.tmpdir, password)
            cmd = viewer_command(viewer, host, port, title(conn), profile, password_file)
            if shutil.which(cmd[0]) is None:
                raise launcher.LaunchError(tr("Programm „{v}“ wurde nicht gefunden (VNC-Viewer „{name}“).", v=cmd[0], name=viewer.name))
            kwargs: dict = {}
            if IS_WINDOWS:
                kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            else:
                kwargs["start_new_session"] = True
            try:
                sess.proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                             env=runtime.clean_env(), **kwargs)
            except OSError as exc:
                raise launcher.LaunchError(tr("VNC-Viewer konnte nicht gestartet werden: {exc}", exc=exc)) from exc
        except Exception:
            self._cleanup_files(sess)
            raise
        sess.state = VIEWING
        sess.viewer_started = time.monotonic()
        return events

    # --- Überwachung (jede Sekunde) ---
    def poll(self, cfg: Config) -> list[Event]:
        events: list[Event] = []
        now = time.monotonic()
        for conn_id, sess in list(self.sessions.items()):
            conn = cfg.connection_by_id(conn_id)
            tunnel = self.tunnels.get(conn_id, sess.forward.id) if sess.forward else None
            if conn is None:
                self._close(conn_id, terminate=True)
                continue
            name = title(conn)
            if sess.state == STARTING:
                if tunnel is None or tunnel.state == tunnels.STOPPED:
                    self._close(conn_id)
                elif tunnel.state == tunnels.FAILED:
                    events.append((tr("VNC-Tunnel zu „{name}“ wurde beendet: {error}", name=name, error=tunnel.error), True))
                    self._close(conn_id)
                elif tunnel.state == tunnels.RUNNING:
                    try:
                        events += self._launch(cfg, conn, sess)
                    except launcher.LaunchError as exc:
                        events.append((str(exc), True))
                        self._close(conn_id)
                elif now - sess.started > START_TIMEOUT:
                    events.append((tr("Der VNC-Tunnel zu „{name}“ ist nicht rechtzeitig aktiv geworden.", name=name), True))
                    self._close(conn_id)
                continue
            if sess.password_file and now - sess.viewer_started > PASSWORD_FILE_TTL:
                self._remove_password_file(sess)
            if tunnel is not None and tunnel.state in (tunnels.FAILED, tunnels.STOPPED):
                if tunnel.state == tunnels.FAILED:
                    events.append((tr("VNC-Tunnel zu „{name}“ wurde beendet: {error}", name=name, error=tunnel.error), True))
                self._close(conn_id)
                continue
            if sess.state == VIEWING and sess.proc is not None:
                code = sess.proc.poll()
                if code is None:
                    continue
                handed_over = code == 0 and now - sess.viewer_started < DETACH_SECONDS
                if handed_over and sess.mode == "tunnel":
                    sess.state = DETACHED  # eine schon laufende Viewer-Instanz hat übernommen: Tunnel bleibt bis „VNC beenden“
                    events.append((tr("VNC läuft im Viewer weiter. Der Tunnel bleibt bis „VNC beenden“ bestehen."), False))
                else:
                    self._close(conn_id)
        return events

    # --- Ende ---
    def stop(self, conn_id: str) -> None:
        """„VNC beenden“: Viewer und Tunnel beenden."""
        self._close(conn_id, terminate=True)

    def stop_all(self) -> None:
        for conn_id in list(self.sessions):
            self._close(conn_id)

    def _close(self, conn_id: str, terminate: bool = False) -> None:
        sess = self.sessions.pop(conn_id, None)
        if sess is None:
            return
        sess.state = CLOSED
        if terminate and sess.proc is not None and sess.proc.poll() is None:
            sess.proc.terminate()
        if sess.forward is not None:
            self.tunnels.stop(conn_id, sess.forward.id)
        self._cleanup_files(sess)

    @staticmethod
    def _remove_password_file(sess: Session) -> None:
        if sess.password_file:
            try:
                os.unlink(sess.password_file)
            except OSError:
                pass
            sess.password_file = None

    def _cleanup_files(self, sess: Session) -> None:
        self._remove_password_file(sess)
        if sess.tmpdir:
            shutil.rmtree(sess.tmpdir, ignore_errors=True)
            sess.tmpdir = None
