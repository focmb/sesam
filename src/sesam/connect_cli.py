"""Wird im Terminal ausgeführt: startet ssh für eine gespeicherte Verbindung (mit Passwort aus dem Schlüsselbund)."""
from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import replace

from . import askpass, launcher, passwords, runtime, storage
from .askpass import ENV_VAR
from .models import Connection


def prepare(conn: Connection, password: str | None, askpass_script: str, base_env: dict[str, str] | None = None):
    """Baut ssh-Argumente und Umgebung. Rein funktional, daher testbar."""
    use_sudo_pw = launcher.wants_sudo_password(conn, password)
    args = launcher.build_ssh_args(conn, sudo_password=use_sudo_pw)
    env = runtime.clean_env(base_env)
    if use_sudo_pw:
        env[launcher.SUDO_ENV] = password
    if password is not None:
        env[ENV_VAR] = password
        env["SSH_ASKPASS"] = askpass_script
        env["SSH_ASKPASS_REQUIRE"] = "force"
        env.setdefault("DISPLAY", "sesam")
        # Askpass kann keine Host-Key-Rückfrage beantworten: unbekannte Hosts automatisch merken,
        # geänderte Schlüssel werden weiterhin abgelehnt.
        if "stricthostkeychecking" not in conn.extra_args.lower():
            args[1:1] = ["-o", "StrictHostKeyChecking=accept-new"]
    return args, env


def _fail(message: str) -> int:
    print(f"Sesam: {message}", file=sys.stderr)
    try:
        input("Enter zum Schließen …")
    except EOFError:
        pass
    return 1


def parse_overrides(argv: list[str]) -> dict[str, str]:
    """Liest --sudo-mode/--sudo-user aus den Wrapper-Argumenten."""
    out: dict[str, str] = {}
    for key, field in (("--sudo-mode", "sudo_mode"), ("--sudo-user", "sudo_user")):
        if key in argv and argv.index(key) + 1 < len(argv):
            out[field] = argv[argv.index(key) + 1]
    return out


def run(conn_id: str, overrides: dict[str, str] | None = None) -> int:
    cfg = storage.load()
    conn = cfg.connection_by_id(conn_id)
    if conn is None:
        return _fail("Verbindung nicht gefunden.")
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
            print("Hinweis: Kein Passwort im Schlüsselbund gefunden, ssh fragt selbst.", file=sys.stderr)
    args, env = prepare(conn, password, askpass.askpass_path() if password is not None else "")
    try:
        if sys.platform.startswith("win"):
            return subprocess.call(args, env=env)
        os.execvpe(args[0], args, env)
    except FileNotFoundError:
        return _fail("ssh wurde nicht gefunden.")
    return 0
