"""Fernzugriff (VNC und RDP): Viewer starten, bei Bedarf durch einen SSH-Tunnel.

Pro Verbindung ein Ziel (``Connection.remote`` = Protokoll, ``Connection.remote_mode`` = Art):

* ``tunnel``: Sesam startet einen kurzlebigen ``ssh -N -L 127.0.0.1:<freier Port>:<Ziel>:<Port>`` (über den Tunnel-Manager,
  also mit Jump-Host, Schlüssel, Passwort und Host-Key-Rückfrage wie bei den Weiterleitungen) und öffnet den Viewer auf
  ``127.0.0.1:<freier Port>``. Endet der Viewer, wird der Tunnel wieder beendet.
* ``direct``: Der Viewer verbindet sich direkt mit ``host:port`` (Geräte, die nur VNC oder RDP sprechen, ohne SSH).

Das Passwort liegt im Schlüsselbund (Schlüssel ``<Verbindungs-ID>:vnc`` bzw. ``:rdp``) und wird dem Viewer nie als Argument übergeben
(sichtbar in der Prozessliste), sondern je nach Viewer über eine Datei (VNC), ein Remmina-Profil oder die Standardeingabe (xfreerdp).
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

from . import launcher, passwords, rfb, runtime, tunnels, viewers
from .i18n import tr
from .models import REMOTE_DEFAULT_PORTS, Config, Connection, Forward, Viewer
from .terminals import current_platform

IS_WINDOWS = sys.platform.startswith("win")

START_TIMEOUT = 25.0  # Sekunden, bis der Tunnel stehen muss
DETACH_SECONDS = 4.0  # beendet sich der Viewer so schnell mit Code 0, hat er die Sitzung an eine laufende Instanz übergeben
PASSWORD_FILE_TTL = 30.0  # Sekunden, die eine Passwortdatei für den Viewer lesbar bleibt

STARTING, VIEWING, DETACHED, CLOSED = "starting", "viewing", "detached", "closed"
ACTIVE = (STARTING, VIEWING, DETACHED)


def label(protocol: str) -> str:
    return protocol.upper()


def port_of(conn: Connection) -> int:
    return conn.remote_port or REMOTE_DEFAULT_PORTS.get(conn.remote, 0)


def password_key(conn_id: str, protocol: str = "vnc") -> str:
    """Schlüssel des Passworts im Schlüsselbund (getrennt vom SSH-Passwort der Verbindung und je Protokoll)."""
    return f"{conn_id}:{protocol}"


def apply_password_change(conn: Connection, change: str | None) -> None:
    """Wendet eine Änderung des Passworts des Fernzugriffs an (None = unverändert, "" = löschen, sonst neues Passwort)."""
    if change is None or not conn.remote:
        return
    if change:
        passwords.set_password(password_key(conn.id, conn.remote), change)
        conn.remote_use_password = True
    else:
        passwords.delete_password(password_key(conn.id, conn.remote))
        conn.remote_use_password = False


def forget_passwords(conn_id: str) -> None:
    """Entfernt die gespeicherten Passwörter beider Protokolle (beim Löschen einer Verbindung)."""
    error = None
    for protocol in ("vnc", "rdp"):
        try:
            passwords.delete_password(password_key(conn_id, protocol))
        except passwords.PasswordError as exc:
            error = exc
    if error is not None:
        raise error


# ---------------------------------------------------------------- Ziel und Tunnel

def free_port() -> int:
    """Freier Port auf 127.0.0.1. Wird er bis zum Start von ssh belegt, scheitert ssh (ExitOnForwardFailure) mit lesbarer Meldung."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def tunnel_forward(conn: Connection, local_port: int) -> Forward:
    """Kurzlebige Weiterleitung für den Fernzugriff (wird nicht in der Konfiguration gespeichert)."""
    return Forward(kind="local", listen_port=local_port, listen_host="127.0.0.1",
                   dest_host=conn.remote_host.strip() or "localhost", dest_port=port_of(conn), name=label(conn.remote))


def title(conn: Connection) -> str:
    return conn.name or conn.host


def kinds(conn: Connection) -> list[str]:
    """Arten der Verbindung für die Anzeige: SSH, SSH mit VNC oder RDP, bei Direktzugriff nur VNC oder RDP."""
    if conn.remote in ("vnc", "rdp") and conn.remote_mode == "direct":
        return [label(conn.remote)]
    return ["SSH"] + ([label(conn.remote)] if conn.remote in ("vnc", "rdp") else [])


# ---------------------------------------------------------------- Viewer wählen und Befehl bauen

def resolve_viewer(cfg: Config, conn: Connection) -> Viewer:
    protocol = conn.remote
    viewer = cfg.viewer_by_id(conn.remote_viewer_id) if conn.remote_viewer_id else None
    if viewer is None or viewer.protocol != protocol:
        viewer = cfg.viewer_by_id(cfg.default_viewer_for(protocol))
    if viewer is None or viewer.protocol != protocol:
        usable = [v for v in cfg.viewers if v.protocol == protocol and v.platform in ("all", current_platform())]
        viewer = usable[0] if usable else None
    if viewer is None:
        raise launcher.LaunchError(tr("Es ist kein {proto}-Viewer konfiguriert. Bitte unter „Viewer“ einen anlegen.", proto=label(protocol)))
    return viewer


def viewer_available(viewer: Viewer) -> bool:
    """Ist das Programm des Viewers vorhanden? (Der eingebaute Viewer ist es immer; leere Befehle meldet ``viewer_command``.)"""
    if viewers.is_builtin(viewer):
        return True
    tokens = launcher.split_command(viewer.command)
    return not tokens or shutil.which(tokens[0]) is not None


def ensure_viewer(cfg: Config, protocol: str, viewer: Viewer, allow_builtin: bool) -> tuple[Viewer, list["Event"]]:
    """Fehlt das Programm des gewählten Viewers, nimmt Sesam für diesen Start den ersten gefundenen Viewer des Protokolls
    (bei VNC zuletzt den eingebauten) und weist auf das fehlende Paket hin. Die Einstellung bleibt unverändert."""
    if viewer_available(viewer) and not (viewers.is_builtin(viewer) and not allow_builtin):
        return viewer, []
    tokens = launcher.split_command(viewer.command)
    wanted = tokens[0] if tokens else viewer.name
    candidates = [v for v in cfg.viewers if v is not viewer and v.protocol == protocol and v.platform in ("all", current_platform())]
    if protocol == "vnc" and allow_builtin and not any(viewers.is_builtin(v) for v in candidates):
        candidates.append(viewers.builtin_viewer())
    for other in candidates:
        if viewers.is_builtin(other) and not allow_builtin:
            continue
        if viewer_available(other):
            note = tr("{proto}-Viewer „{wanted}“ wurde nicht gefunden. Stattdessen wird „{used}“ verwendet.", proto=label(protocol), wanted=viewer.name, used=other.name)
            package = viewers.package_for(wanted)
            if package:
                note += " " + tr("Zum Installieren: Paket „{package}“.", package=package)
            return other, [(note, False)]
    hints = viewers.package_hints(protocol)
    if hints:
        raise launcher.LaunchError(tr("Kein {proto}-Viewer gefunden. Bitte eines dieser Pakete installieren: {packages}.", proto=label(protocol), packages=", ".join(hints)))
    return viewer, []  # der Fehler „Programm nicht gefunden“ entsteht beim Start


def uses_profile(viewer: Viewer) -> bool:
    return "{profile}" in viewer.command


def uses_password_file(viewer: Viewer) -> bool:
    return "{password_file}" in viewer.command


def uses_rdp_file(viewer: Viewer) -> bool:
    return "{rdp_file}" in viewer.command


def uses_stdin_password(viewer: Viewer) -> bool:
    """xfreerdp liest das Passwort mit ``/from-stdin`` von der Standardeingabe."""
    return any(t == "/from-stdin" or t.startswith("/from-stdin:") for t in launcher.split_command(viewer.command))


def viewer_command(viewer: Viewer, host: str, port: int, name: str, profile: str | None = None,
                   password_file: str | None = None, user: str = "", domain: str = "", rdp_file: str | None = None,
                   extra: str = "") -> list[str]:
    """Befehl des Viewers als Argumentliste (nie über eine Shell).

    Argumente mit einem leeren Platzhalter (``{user}``, ``{domain}``, ``{rdp_file}``, ``{password_file}``) entfallen; bei ``{password_file}``
    auch die davorstehende Option (``-passwd {password_file}``). ``extra`` (Zusatzargumente der Verbindung) wird hinten angehängt."""
    scheme = "rdp" if viewer.protocol == "rdp" else "vnc"
    ctx = {"{host}": host, "{port}": str(port), "{display}": str(port - 5900), "{url}": f"{scheme}://{host}:{port}", "{title}": name,
           "{user}": user, "{domain}": domain}
    for key, value in (("{profile}", profile), ("{password_file}", password_file), ("{rdp_file}", rdp_file)):
        ctx[key] = value if value is not None else ""
    tokens = launcher.split_command(viewer.command)
    out: list[str] = []
    for i, token in enumerate(tokens):
        empty = [k for k in ("{user}", "{domain}", "{password_file}", "{rdp_file}") if k in token and not ctx[k]]
        if empty:
            # ohne Wert entfällt das Argument samt seiner Option („-passwd {password_file}“)
            if token == "{password_file}" and out and i and tokens[i - 1] == out[-1] and out[-1].startswith("-") and "{" not in out[-1]:
                out.pop()
            continue
        for key, value in ctx.items():
            token = token.replace(key, value)
        out.append(token)
    if not out:
        raise launcher.LaunchError(tr("Die Befehlsvorlage von „{name}“ ist leer.", name=viewer.name))
    out += launcher.split_command(extra) if extra.strip() else []
    return out


def check_target(conn: Connection, host: str) -> None:
    """Host und Port dürfen nicht wie eine Option aussehen (der Wert landet als Argument im Viewer-Aufruf)."""
    launcher._check_target_part(host, tr("Host"))
    if not 1 <= port_of(conn) <= 65535:
        raise launcher.LaunchError(tr("Ungültiger Wert für {what}: {value}", what=tr("Port"), value=repr(conn.remote_port)))


# ---------------------------------------------------------------- Passwortübergabe

_VNC_DES_KEY = bytes([0xE8, 0x4A, 0xD6, 0x60, 0xC4, 0x72, 0x1A, 0xE0])  # festes Schlüssel von vncpasswd (bitweise gespiegelt)


_des = rfb.des_cipher  # gemeinsame DES-Hilfe (auch für die Anmeldung des eingebauten Viewers)


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


def remmina_profile(name: str, host: str, port: int, password: str | None = None, secret: bytes | None = None,
                    protocol: str = "vnc", user: str = "", domain: str = "") -> str:
    lines = ["[remmina]", f"name={_ini_value(name)}", f"protocol={protocol.upper()}", f"server={_ini_value(host)}:{port}"]
    if protocol == "rdp":
        if user:
            lines.append(f"username={_ini_value(user)}")
        if domain:
            lines.append(f"domain={_ini_value(domain)}")
    if password and secret:
        lines.append(f"password={remmina_encrypt(password, secret)}")
    return "\n".join(lines) + "\n"


def rdp_file_text(host: str, port: int, user: str = "", domain: str = "") -> str:
    """Inhalt einer .rdp-Datei (für mstsc). Das Passwort steht nie darin."""
    lines = [f"full address:s:{_ini_value(host)}:{port}"]
    if user:
        account = f"{_ini_value(domain)}\\{_ini_value(user)}" if domain else _ini_value(user)
        lines.append(f"username:s:{account}")
    return "\r\n".join(lines) + "\r\n"


# ---------------------------------------------------------------- Sitzungen

@dataclass
class Session:
    conn_id: str
    mode: str
    viewer: Viewer
    protocol: str = "vnc"
    port: int = 0  # lokaler Tunnelport (nur "tunnel")
    forward: Forward | None = None
    state: str = STARTING
    started: float = field(default_factory=time.monotonic)
    viewer_started: float = 0.0
    proc: subprocess.Popen | None = None
    tmpdir: str | None = None
    password_file: str | None = None
    window: object | None = None  # eingebauter Viewer: sein Fenster (``is_open()``, ``end()``)


Event = tuple[str, bool]  # (Text, Fehler?)


class Manager:
    """Alle Sitzungen des Fernzugriffs (VNC und RDP) dieser Sesam-Sitzung, je Verbindung höchstens eine."""

    def __init__(self, tunnel_manager: tunnels.Manager):
        self.tunnels = tunnel_manager
        self.sessions: dict[str, Session] = {}
        self.open_builtin = None  # von der Oberfläche gesetzt: ``(conn, host, port, password, direct) -> Fenster`` des eingebauten Viewers

    def is_open(self, conn_id: str) -> bool:
        s = self.sessions.get(conn_id)
        return s is not None and s.state in ACTIVE

    # --- Start ---
    def begin(self, cfg: Config, conn: Connection, start_tunnel) -> list[Event]:
        """Startet den Fernzugriff für die Verbindung. ``start_tunnel(conn, forward)`` startet den kurzlebigen Tunnel (Rückfragen, Passwort) und liefert False bei Abbruch.

        Bei ``direct`` startet der Viewer sofort, bei ``tunnel`` erst, wenn ``poll`` den Tunnel als aktiv meldet."""
        if conn.remote not in ("vnc", "rdp"):
            raise launcher.LaunchError(tr("Für diese Verbindung ist der Fernzugriff nicht eingeschaltet."))
        proto = label(conn.remote)
        if self.is_open(conn.id):
            return [(tr("{proto} zu „{name}“ ist bereits geöffnet.", proto=proto, name=title(conn)), False)]
        viewer, notes = ensure_viewer(cfg, conn.remote, resolve_viewer(cfg, conn), self.open_builtin is not None)
        if conn.remote_mode == "direct":
            check_target(conn, conn.host)
            sess = Session(conn.id, "direct", viewer, protocol=conn.remote)
            events = notes + self._launch(cfg, conn, sess)
            self.sessions[conn.id] = sess
            return events
        port = free_port()
        forward = tunnel_forward(conn, port)
        sess = Session(conn.id, "tunnel", viewer, protocol=conn.remote, port=port, forward=forward)
        if not start_tunnel(conn, forward):
            return []
        self.sessions[conn.id] = sess
        return notes + [(tr("{proto}-Tunnel wird gestartet: {name}", proto=proto, name=title(conn)), False)]

    def _launch(self, cfg: Config, conn: Connection, sess: Session) -> list[Event]:
        events: list[Event] = []
        proto = label(sess.protocol)
        host, port = ("127.0.0.1", sess.port) if sess.mode == "tunnel" else (conn.host, port_of(conn))
        password = None
        if conn.remote_use_password:
            try:
                password = passwords.get_password(password_key(conn.id, sess.protocol))
            except passwords.PasswordError as exc:
                events.append((tr("{proto}-Passwort nicht gelesen: {exc}", proto=proto, exc=exc), True))
        viewer = sess.viewer
        if viewers.is_builtin(viewer):  # eingebauter Viewer: das Passwort bleibt im Programm, es gibt weder Datei noch Prozess
            if self.open_builtin is None or sess.protocol != "vnc":
                raise launcher.LaunchError(tr("Der eingebaute Viewer ist hier nicht verfügbar."))
            sess.window = self.open_builtin(conn, host, port, password, sess.mode == "direct")
            sess.state = VIEWING
            sess.viewer_started = time.monotonic()
            return events
        rdp = sess.protocol == "rdp"
        profile = password_file = rdp_file = None
        stdin_password = password if (rdp and password and uses_stdin_password(viewer)) else None
        user, domain = (conn.remote_user.strip(), conn.remote_domain.strip()) if rdp else ("", "")
        want_file = uses_password_file(viewer) and password and not rdp
        try:
            if uses_profile(viewer) or want_file or (rdp and uses_rdp_file(viewer)):
                sess.tmpdir = tempfile.mkdtemp(prefix="sesam-remote-")  # nur für den Benutzer lesbar (0700)
            if uses_profile(viewer):
                secret = remmina_secret() if password else None
                if password and secret is None:
                    events.append((tr("Das Passwort konnte Remmina nicht übergeben werden (Remmina-Schlüssel nicht gefunden). Remmina fragt danach."), True))
                profile = os.path.join(sess.tmpdir, "sesam.remmina")
                fd = os.open(profile, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(remmina_profile(title(conn), host, port, password, secret, sess.protocol, user, domain))
            if want_file:
                password_file = sess.password_file = write_password_file(sess.tmpdir, password)
            if rdp and uses_rdp_file(viewer):
                rdp_file = os.path.join(sess.tmpdir, "sesam.rdp")
                fd = os.open(rdp_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
                    fh.write(rdp_file_text(host, port, user, domain))
            if password and not (profile or password_file or stdin_password):
                events.append((tr("Dieser Viewer übernimmt das gespeicherte Passwort nicht; er fragt danach."), False))
            cmd = viewer_command(viewer, host, port, title(conn), profile, password_file, user, domain, rdp_file,
                                 conn.remote_args if rdp else "")
            if shutil.which(cmd[0]) is None:
                raise launcher.LaunchError(tr("Programm „{v}“ wurde nicht gefunden ({proto}-Viewer „{name}“).", v=cmd[0], proto=proto, name=viewer.name))
            kwargs: dict = {}
            if IS_WINDOWS:
                kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            else:
                kwargs["start_new_session"] = True
            try:
                sess.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE if stdin_password else subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                             stderr=subprocess.DEVNULL, env=runtime.clean_env(), **kwargs)
            except OSError as exc:
                raise launcher.LaunchError(tr("{proto}-Viewer konnte nicht gestartet werden: {exc}", proto=proto, exc=exc)) from exc
            if stdin_password:
                try:
                    sess.proc.stdin.write((stdin_password + "\n").encode("utf-8"))
                    sess.proc.stdin.close()
                except OSError:  # der Viewer hat die Eingabe schon geschlossen
                    pass
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
                    events.append((tr("{proto}-Tunnel zu „{name}“ wurde beendet: {error}", proto=label(sess.protocol), name=name, error=tunnel.error), True))
                    self._close(conn_id)
                elif tunnel.state == tunnels.RUNNING:
                    try:
                        events += self._launch(cfg, conn, sess)
                    except launcher.LaunchError as exc:
                        events.append((str(exc), True))
                        self._close(conn_id)
                elif now - sess.started > START_TIMEOUT:
                    events.append((tr("Der {proto}-Tunnel zu „{name}“ ist nicht rechtzeitig aktiv geworden.", proto=label(sess.protocol), name=name), True))
                    self._close(conn_id)
                continue
            if sess.password_file and now - sess.viewer_started > PASSWORD_FILE_TTL:
                self._remove_password_file(sess)
            if tunnel is not None and tunnel.state in (tunnels.FAILED, tunnels.STOPPED):
                if tunnel.state == tunnels.FAILED:
                    events.append((tr("{proto}-Tunnel zu „{name}“ wurde beendet: {error}", proto=label(sess.protocol), name=name, error=tunnel.error), True))
                self._close(conn_id)
                continue
            if sess.state == VIEWING and sess.window is not None:
                if not sess.window.is_open():  # Fenster geschlossen: Sitzung und Tunnel enden
                    self._close(conn_id)
                continue
            if sess.state == VIEWING and sess.proc is not None:
                code = sess.proc.poll()
                if code is None:
                    continue
                handed_over = code == 0 and now - sess.viewer_started < DETACH_SECONDS
                if handed_over and sess.mode == "tunnel":
                    sess.state = DETACHED  # eine schon laufende Viewer-Instanz hat übernommen: Tunnel bleibt bis „Beenden“
                    events.append((tr("{proto} läuft im Viewer weiter. Der Tunnel bleibt bis „{proto} beenden“ bestehen.", proto=label(sess.protocol)), False))
                else:
                    self._close(conn_id)
        return events

    # --- Ende ---
    def stop(self, conn_id: str) -> None:
        """„Beenden“: Viewer und Tunnel beenden."""
        self._close(conn_id, terminate=True)

    def stop_all(self) -> None:
        for conn_id in list(self.sessions):
            self._close(conn_id)

    def _close(self, conn_id: str, terminate: bool = False) -> None:
        sess = self.sessions.pop(conn_id, None)
        if sess is None:
            return
        sess.state = CLOSED
        if sess.window is not None:
            try:
                sess.window.end()
            finally:
                sess.window = None
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
