"""Wird im Terminal ausgeführt: startet ssh für eine gespeicherte Verbindung (mit Passwort aus dem Schlüsselbund)."""
from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import sys
import threading
from dataclasses import replace
from pathlib import Path

from . import askpass, launcher, passwords, runtime, storage
from .askpass import ENV_VAR
from .models import Connection
from .i18n import tr


def prepare(conn: Connection, password: str | None, askpass_script: str, base_env: dict[str, str] | None = None):
    """Baut ssh-Argumente und Umgebung. Rein funktional, daher testbar."""
    use_sudo_pw = launcher.wants_sudo_password(conn, password)
    args = launcher.build_ssh_args(conn, sudo_password=use_sudo_pw)
    env = runtime.clean_env(base_env)
    if use_sudo_pw:
        env[launcher.SUDO_ENV] = password
    if password is not None:
        env[ENV_VAR] = password
        env[askpass.HOST_VAR] = conn.host
        env["SSH_ASKPASS"] = askpass_script
        env["SSH_ASKPASS_REQUIRE"] = "force"
        env.setdefault("DISPLAY", "sesam")
        # Askpass kann keine Host-Key-Rückfrage beantworten. Unbekannte Hosts bestätigt run() vorher mit Fingerabdruck
        # (ensure_known_hosts); danach prüft ssh ganz normal, geänderte Schlüssel werden abgelehnt.
    return args, env


SSH_ERROR = 255  # Exit-Code von ssh bei eigenen Fehlern (Verbindung, Host-Key, Anmeldung)
HOSTKEY_CHANGED = "REMOTE HOST IDENTIFICATION HAS CHANGED"
_CAPTURE_LIMIT = 64 * 1024


def host_key_changed(stderr: str) -> bool:
    """Hat ssh die Verbindung wegen eines geänderten Host-Schlüssels abgelehnt?"""
    return HOSTKEY_CHANGED in stderr


def _bare_host(name: str) -> str:
    """Hostname ohne Benutzer, Klammern und Port („user@[h]:22“ → „h“)."""
    name = name.strip().rsplit("@", 1)[-1]
    m = re.fullmatch(r"\[(.+)\](?::\d+)?", name)
    if m:
        return m.group(1).lower()
    return re.sub(r":\d+$", "", name).lower() if name.count(":") == 1 else name.lower()


def known_hosts_name(conn: Connection, stderr: str = "") -> str:
    """Name, unter dem der Host in known_hosts steht. Bei einem Sprunghost nennt ssh den betroffenen Host selbst.

    Ein Server kann in seinem Banner jede Meldung vortäuschen; deshalb zählt der von ssh genannte Name nur, wenn er zu
    dieser Verbindung gehört (Ziel oder Jump-Host). Sonst würde die Rückfrage den Eintrag eines fremden Hosts entfernen."""
    m = re.search(r"Host key for (\S+) has changed", stderr)
    if m:
        own = {_bare_host(conn.host), *(_bare_host(p) for p in conn.jump_host.split(",") if p.strip())}
        if _bare_host(m.group(1)) in own and not m.group(1).startswith("-"):
            return m.group(1)
    host = conn.host.strip()
    return host if (conn.port or 22) == 22 else f"[{host}]:{conn.port}"


def forget_command(name: str) -> list[str]:
    return ["ssh-keygen", "-R", name]


def _split_target(part: str, default_port: int = 22) -> tuple[str, int]:
    """„user@host:2200“ / „user@[::1]:2200“ → („host“, 2200)."""
    part = part.strip().rsplit("@", 1)[-1]
    m = re.fullmatch(r"\[(.+)\](?::(\d+))?", part)
    if m:
        return m.group(1), int(m.group(2) or default_port)
    if part.count(":") == 1:
        host, _, port = part.partition(":")
        if port.isdigit():
            return host, int(port)
    return part, default_port


def host_key_targets(conn: Connection) -> list[tuple[str, int]]:
    """Alle Hosts, deren Schlüssel ssh prüft: Ziel und Jump-Hosts."""
    out = [(conn.host.strip(), conn.port or 22)]
    out += [_split_target(p) for p in conn.jump_host.split(",") if p.strip()]
    return out


def _kh_name(host: str, port: int) -> str:
    return host if port == 22 else f"[{host}]:{port}"


def _is_known(name: str) -> bool:
    r = subprocess.run(["ssh-keygen", "-F", name], stdin=subprocess.DEVNULL, capture_output=True, text=True)
    return r.returncode == 0 and bool(r.stdout.strip())


def known_hosts_file() -> Path:
    return Path.home() / ".ssh" / "known_hosts"


def ensure_known_hosts(conn: Connection) -> bool:
    """Unbekannte Hosts (Ziel und Jump-Hosts) erst nach Bestätigung des Fingerabdrucks in known_hosts eintragen.

    Nötig, weil bei der automatischen Passworteingabe niemand ssh' Host-Key-Frage beantworten kann – und ein stilles
    „accept-new“ das Passwort beim ersten Kontakt an einen Man-in-the-Middle geben würde. False = Verbindung abbrechen."""
    if not (shutil.which("ssh-keygen") and shutil.which("ssh-keyscan")):
        return True  # ohne die Hilfsprogramme entscheidet ssh selbst (und scheitert bei unbekannten Hosts)
    for host, port in host_key_targets(conn):
        name = _kh_name(host, port)
        if not host or _is_known(name):
            continue
        scan = subprocess.run(["ssh-keyscan", "-T", "10", "-p", str(port), "--", host], stdin=subprocess.DEVNULL,
                              capture_output=True, text=True)
        lines = [ln for ln in scan.stdout.splitlines() if ln.strip() and not ln.startswith("#")]
        if not lines:
            print(tr("Der Schlüssel von {host} konnte nicht abgerufen werden.", host=name), file=sys.stderr)
            return False
        shown = subprocess.run(["ssh-keygen", "-lf", "-"], input="\n".join(lines) + "\n", capture_output=True, text=True).stdout
        print(file=sys.stderr)
        print(tr("Der Host {host} ist noch nicht bekannt. Fingerabdrücke:", host=name), file=sys.stderr)
        print(shown.rstrip(), file=sys.stderr)
        print(tr("Nur fortfahren, wenn ein Fingerabdruck stimmt – sonst geht das Passwort an einen Fremden."), file=sys.stderr)
        if not _ask(tr("Host vertrauen und in known_hosts eintragen? [j/N]")):
            return False
        path = known_hosts_file()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            needs_nl = path.exists() and path.stat().st_size > 0 and not path.read_bytes().endswith(b"\n")
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(("\n" if needs_nl else "") + "\n".join(lines) + "\n")
        except OSError as exc:
            print(tr("known_hosts kann nicht geschrieben werden: {exc}", exc=exc), file=sys.stderr)
            return False
    return True


def run_ssh(args: list[str], env: dict[str, str]) -> tuple[int, str]:
    """Startet ssh als Unterprozess. Eingabe und Ausgabe bleiben am Terminal, stderr wird angezeigt und zugleich mitgelesen."""
    proc = subprocess.Popen(args, env=env, stderr=subprocess.PIPE)
    captured = bytearray()

    def pump():
        fd = proc.stderr.fileno()
        out = getattr(sys.stderr, "buffer", None)
        while True:
            try:
                chunk = os.read(fd, 4096)
            except OSError:
                break
            if not chunk:
                break
            if len(captured) < _CAPTURE_LIMIT:
                captured.extend(chunk)
            try:
                if out is not None:
                    out.write(chunk)
                    out.flush()
                else:
                    sys.stderr.write(chunk.decode("utf-8", "replace"))
                    sys.stderr.flush()
            except (OSError, ValueError):
                pass

    thread = threading.Thread(target=pump, daemon=True)
    thread.start()
    old = signal.signal(signal.SIGINT, lambda *_: None)  # Strg+C gilt ssh bzw. der Sitzung, nicht dem Wrapper (Handler statt SIG_IGN, damit ssh ihn erbt)
    try:
        code = proc.wait()
    finally:
        signal.signal(signal.SIGINT, old)
    thread.join(timeout=2)
    return code, captured.decode("utf-8", "replace")


def _ask(question: str) -> bool:
    try:
        answer = input(question + " ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return answer in ("j", "ja", "y", "yes")


def _wait_for_enter() -> None:
    try:
        input(tr("Enter zum Schließen …"))
    except (EOFError, KeyboardInterrupt):
        pass


def forget_host_key(conn: Connection, stderr: str) -> bool:
    """Fragt, ob der alte known_hosts-Eintrag entfernt werden soll, und entfernt ihn. True = entfernt, neu verbinden."""
    name = known_hosts_name(conn, stderr)
    print(file=sys.stderr)
    print(tr("Der Schlüssel von {host} hat sich geändert, z. B. weil dort ein neuer Server mit gleicher Adresse läuft. "
             "Es könnte aber auch ein Angriff sein.", host=name), file=sys.stderr)
    if not shutil.which("ssh-keygen"):
        print(tr("ssh-keygen wurde nicht gefunden. Entferne den Eintrag von Hand aus known_hosts."), file=sys.stderr)
        return False
    if not _ask(tr("Alten Eintrag aus known_hosts entfernen und neu verbinden? [j/N]")):
        return False
    result = subprocess.run(forget_command(name), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        print(tr("Der Eintrag konnte nicht entfernt werden: {exc}", exc=(result.stderr or "").strip()), file=sys.stderr)
        return False
    print(tr("Eintrag entfernt, verbinde neu …"), file=sys.stderr)
    return True


def _fail(message: str) -> int:
    print(f"Sesam: {message}", file=sys.stderr)
    try:
        input(tr("Enter zum Schließen …"))
    except (EOFError, KeyboardInterrupt):
        pass
    return 1


def parse_overrides(argv: list[str]) -> dict[str, str]:
    """Liest --sudo-mode/--sudo-user/--remote-command aus den Wrapper-Argumenten."""
    out: dict[str, str] = {}
    for key, field in (("--sudo-mode", "sudo_mode"), ("--sudo-user", "sudo_user"), ("--remote-command", "remote_command")):
        if key in argv and argv.index(key) + 1 < len(argv):
            out[field] = argv[argv.index(key) + 1]
    if "remote_command" in out:  # ein ersetzter Befehl (z. B. docker exec) läuft ohne sudo des Hosts
        out.setdefault("sudo_mode", "")
        out.setdefault("sudo_user", "")
    return out


def run(conn_id: str, overrides: dict[str, str] | None = None) -> int:
    try:
        cfg = storage.load()
    except storage.ConfigError as exc:
        return _fail(tr("Die Konfiguration ist beschädigt ({reason}).\nStarte Sesam, um sie zu sichern, oder repariere {path}.", reason=exc.reason, path=exc.path))
    except OSError as exc:
        return _fail(tr("Die Konfiguration kann nicht gelesen werden: {exc}", exc=exc))
    conn = cfg.connection_by_id(conn_id)
    if conn is None:
        return _fail(tr("Verbindung nicht gefunden."))
    if overrides:
        conn = replace(conn, **overrides)
    conn, password_key = launcher.resolve(cfg, conn)
    password = None
    if conn.use_password:
        try:
            password = passwords.get_password(password_key)
        except passwords.PasswordError as exc:
            return _fail(str(exc))
        if password is None:
            print(tr("Hinweis: Kein Passwort im Schlüsselbund gefunden, ssh fragt selbst."), file=sys.stderr)
    try:
        args, env = prepare(conn, password, askpass.askpass_path() if password is not None else "")
    except launcher.LaunchError as exc:
        return _fail(str(exc))
    except OSError as exc:
        return _fail(tr("Die Verbindung kann nicht vorbereitet werden: {exc}", exc=exc))
    if password is not None and "stricthostkeychecking" not in conn.extra_args.lower() and not ensure_known_hosts(conn):
        return _fail(tr("Verbindung abgebrochen: Der Host-Schlüssel wurde nicht bestätigt."))
    try:
        code, err = run_ssh(args, env)
        if code == SSH_ERROR and host_key_changed(err) and forget_host_key(conn, err):
            code, err = run_ssh(args, env)  # nur ein zweiter Versuch
    except FileNotFoundError:
        return _fail(tr("ssh wurde nicht gefunden."))
    if code == SSH_ERROR:  # ssh selbst ist gescheitert (oder die Sitzung wurde getrennt): Fenster offen lassen, damit die Meldung lesbar bleibt
        print("\n" + tr("Die Verbindung zu {host} konnte nicht aufgebaut werden oder wurde beendet (ssh-Exit-Code {code}).", host=conn.host, code=code), file=sys.stderr)
        _wait_for_enter()
    return code
