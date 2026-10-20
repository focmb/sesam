"""Wird im Terminal ausgeführt: startet ssh für eine gespeicherte Verbindung (mit Passwort aus dem Schlüsselbund)."""
from __future__ import annotations

import os
import subprocess
import sys

from . import askpass, launcher, passwords, storage
from .askpass import ENV_VAR
from .models import Connection


def prepare(conn: Connection, password: str | None, askpass_script: str, base_env: dict[str, str] | None = None):
    """Baut ssh-Argumente und Umgebung. Rein funktional, daher testbar."""
    args = launcher.build_ssh_args(conn)
    env = dict(os.environ if base_env is None else base_env)
    if password is not None:
        env[ENV_VAR] = password
        env["SSH_ASKPASS"] = askpass_script
        env["SSH_ASKPASS_REQUIRE"] = "force"
        env.setdefault("DISPLAY", "ssh-verwaltung")
        # Askpass kann keine Host-Key-Rückfrage beantworten: unbekannte Hosts automatisch merken,
        # geänderte Schlüssel werden weiterhin abgelehnt.
        if "stricthostkeychecking" not in conn.extra_args.lower():
            args[1:1] = ["-o", "StrictHostKeyChecking=accept-new"]
    return args, env


def _fail(message: str) -> int:
    print(f"SSH-Verwaltung: {message}", file=sys.stderr)
    try:
        input("Enter zum Schließen …")
    except EOFError:
        pass
    return 1


def run(conn_id: str) -> int:
    conn = storage.load().connection_by_id(conn_id)
    if conn is None:
        return _fail("Verbindung nicht gefunden.")
    password = None
    if conn.use_password:
        try:
            password = passwords.get_password(conn.id)
        except passwords.PasswordError as exc:
            return _fail(str(exc))
        if password is None:
            print("Hinweis: Kein Passwort im Schlüsselbund gefunden, ssh fragt selbst.", file=sys.stderr)
    args, env = prepare(conn, password, askpass.askpass_path() if password is not None else "")
    try:
        if sys.platform.startswith("win"):
            return subprocess.call(args, env=env)
        os.execvpe(args[0], args, env)
    except FileNotFoundError:
        return _fail("ssh wurde nicht gefunden.")
    return 0
