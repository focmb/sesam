from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys

from dataclasses import replace

from . import runtime
from .models import Config, Connection, Terminal
from .terminals import current_platform

IS_WINDOWS = sys.platform.startswith("win")


class LaunchError(Exception):
    pass


def split_command(text: str) -> list[str]:
    """Zerlegt eine Befehlszeile plattformgerecht in Argumente."""
    if IS_WINDOWS:
        parts = shlex.split(text, posix=False)
        return [p[1:-1] if len(p) >= 2 and p[0] == p[-1] and p[0] in "\"'" else p for p in parts]
    return shlex.split(text)


def join_command(args: list[str]) -> str:
    return subprocess.list2cmdline(args) if IS_WINDOWS else shlex.join(args)


def destination(conn: Connection) -> str:
    return f"{conn.user}@{conn.host}" if conn.user else conn.host


SUDO_MODES = {"login": "-i", "shell": "-s"}
SUDO_ENV = "LC_SSHV_PW"  # Träger für das Passwort; Debian/Ubuntu akzeptieren LC_* per AcceptEnv


def wants_sudo_password(conn: Connection, password: str | None) -> bool:
    return bool(password) and bool(SUDO_MODES.get(conn.sudo_mode)) and conn.sudo_use_password


def remote_command_line(conn: Connection, sudo_password: bool = False) -> str:
    """Befehl, der auf dem Zielsystem ausgeführt wird (optional mit sudo davor)."""
    cmd = conn.remote_command.strip()
    flag = SUDO_MODES.get(conn.sudo_mode)
    if flag:
        parts = ["sudo"]
        if conn.sudo_user.strip():
            parts += ["-u", conn.sudo_user.strip()]
        parts.append(flag)
        base = shlex.join(parts)  # Zielsystem ist POSIX
        if sudo_password:
            # Passwort kommt über die SSH-Umgebung (nicht über Argumente). Nur wenn gesetzt, genau ein Versuch;
            # schlägt er fehl, fragt sudo -i/-s normal nach. Die Variable wird für sudo selbst entfernt.
            v = SUDO_ENV
            auth = f"[ -n \"${v}\" ] && printf '%s\\n' \"${v}\" | sudo -S -p '' -v 2>/dev/null; "
            base = f"{auth}env -u {v} {base}"
        cmd = f"{base} {cmd}" if cmd else base
    return cmd


def build_ssh_args(conn: Connection, sudo_password: bool = False) -> list[str]:
    args = ["ssh"]
    if conn.port and conn.port != 22:
        args += ["-p", str(conn.port)]
    if conn.identity_file:
        args += ["-i", os.path.expanduser(conn.identity_file)]
    if conn.jump_host:
        args += ["-J", conn.jump_host]
    if conn.extra_args.strip():
        args += split_command(conn.extra_args)
    remote = remote_command_line(conn, sudo_password)
    if sudo_password:
        args += ["-o", f"SendEnv={SUDO_ENV}"]
    if remote:
        args.append("-t")
    args.append(destination(conn))
    if remote:
        args.append(remote)
    return args


def resolve(cfg: Config, conn: Connection) -> tuple[Connection, str]:
    """Wendet die gewählte Identität an. Liefert die wirksame Verbindung und den Schlüsselbund-Schlüssel des Passworts."""
    ident = cfg.identity_by_id(conn.identity_id) if conn.identity_id else None
    if ident is None:
        return conn, conn.id
    eff = replace(conn, user=ident.user, identity_file=ident.identity_file, use_password=ident.use_password)
    return eff, ident.id


def wrapper_args(conn: Connection) -> list[str]:
    """Befehl, der im Terminal läuft und ssh samt Passwort aus dem Schlüsselbund startet."""
    args = runtime.self_command() + ["--connect", conn.id]
    if conn.sudo_mode:  # auch für einmalige Überschreibungen (Kontextmenü), die nicht gespeichert sind
        args += ["--sudo-mode", conn.sudo_mode, "--sudo-user", conn.sudo_user]
    return args


def resolve_terminal(cfg: Config, conn: Connection) -> Terminal:
    term = cfg.terminal_by_id(conn.terminal_id) if conn.terminal_id else None
    term = term or cfg.terminal_by_id(cfg.default_terminal_id)
    if term is None:
        usable = [t for t in cfg.terminals if t.platform in ("all", current_platform())]
        term = usable[0] if usable else None
    if term is None:
        raise LaunchError("Es ist kein Terminal konfiguriert. Bitte unter „Terminals“ eines anlegen.")
    return term


def build_terminal_command(terminal: Terminal, conn: Connection, ssh_args: list[str] | None = None) -> list[str]:
    ssh_args = ssh_args if ssh_args is not None else build_ssh_args(conn)
    ctx = {
        "{title}": conn.name or conn.host,
        "{host}": conn.host,
        "{user}": conn.user,
        "{port}": str(conn.port),
        "{destination}": destination(conn),
        "{ssh_command}": join_command(ssh_args),
    }
    out: list[str] = []
    for token in split_command(terminal.command):
        if token == "{ssh_args}":
            out.extend(ssh_args)
            continue
        for key, value in ctx.items():
            token = token.replace(key, value)
        out.append(token)
    if not out:
        raise LaunchError(f"Die Befehlsvorlage von „{terminal.name}“ ist leer.")
    return out


def launch(cfg: Config, conn: Connection, terminal: Terminal | None = None) -> list[str]:
    conn, _ = resolve(cfg, conn)
    terminal = terminal or resolve_terminal(cfg, conn)
    cmd = build_terminal_command(terminal, conn, wrapper_args(conn) if conn.use_password else None)
    if shutil.which(cmd[0]) is None:
        raise LaunchError(f"Programm „{cmd[0]}“ wurde nicht gefunden (Terminal „{terminal.name}“).")
    kwargs: dict = {}
    if IS_WINDOWS:
        flags = subprocess.CREATE_NEW_PROCESS_GROUP
        if terminal.new_console:
            flags |= subprocess.CREATE_NEW_CONSOLE
        kwargs["creationflags"] = flags
    else:
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen(
            cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env=runtime.clean_env(), **kwargs,
        )
    except OSError as exc:
        raise LaunchError(f"Terminal konnte nicht gestartet werden: {exc}") from exc
    return cmd
