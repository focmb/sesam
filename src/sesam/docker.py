"""Docker-Container auf einem SSH-Host: per SSH auflisten und eine Shell darin öffnen (``docker exec``)."""
from __future__ import annotations

import json
import shlex
from dataclasses import dataclass, replace
from pathlib import Path

from . import sftp
from .models import Config, Connection

LIST_COMMAND = "docker ps --format '{{json .}}'"  # nur laufende Container; eine JSON-Zeile je Container
# Als Argument von „sh -c“ im Container: bash, falls vorhanden, sonst sh.
AUTO_SHELL = "command -v bash >/dev/null 2>&1 && exec bash || exec sh"
EXEC_TIMEOUT = 20  # Sekunden für die Abfrage selbst (der Verbindungsaufbau hat einen eigenen Timeout)


class DockerError(Exception):
    """Fehler mit einer für Anwender lesbaren Meldung."""


@dataclass(frozen=True)
class Container:
    id: str
    name: str
    image: str
    status: str
    ports: str = ""


def parse_ps(output: str) -> list[Container]:
    """Liest die Ausgabe von ``docker ps --format '{{json .}}'``. Unlesbare Zeilen werden übergangen."""
    found = []
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except ValueError:
            continue
        if not isinstance(data, dict):
            continue
        name = str(data.get("Names", "")).split(",")[0].strip().lstrip("/")
        if not name:
            continue
        found.append(Container(
            id=str(data.get("ID", "")), name=name, image=str(data.get("Image", "")),
            status=str(data.get("Status", "")), ports=str(data.get("Ports", "")),
        ))
    return sorted(found, key=lambda c: c.name.casefold())


def describe_error(status: int, stderr: str) -> str:
    """Macht aus dem Fehler von ``docker ps`` eine verständliche Meldung."""
    text = stderr.strip()
    low = text.lower()
    if status == 127 or "command not found" in low or "docker: not found" in low:
        return "Docker ist auf dem Host nicht installiert (Befehl „docker“ nicht gefunden)."
    if "permission denied" in low:
        return "Kein Zugriff auf Docker: Der Benutzer darf den Docker-Dienst nicht nutzen (Mitglied der Gruppe „docker“?)."
    if "cannot connect to the docker daemon" in low or "is the docker daemon running" in low:
        return "Der Docker-Dienst auf dem Host läuft nicht."
    first = text.splitlines()[0] if text else f"Fehlercode {status}"
    return f"Docker meldet: {first}"


def list_containers(
    cfg: Config,
    conn: Connection,
    password: str | None = None,
    known_hosts: Path | None = None,
    timeout: float = 10.0,
) -> list[Container]:
    """Fragt den Host nach seinen laufenden Containern. Blockiert, gehört in einen Hintergrund-Thread.

    Wirft ``sftp.SftpAuthError`` bei abgelehnter Anmeldung, sonst ``sftp.SftpError`` oder ``DockerError``.
    """
    client, _eff = sftp.open_client(
        cfg, conn, password=password, known_hosts=known_hosts, timeout=timeout, purpose="die Container-Anzeige"
    )
    try:
        try:
            _stdin, out, err = client.exec_command(LIST_COMMAND, timeout=EXEC_TIMEOUT)
            stdout = out.read().decode("utf-8", "replace")
            stderr = err.read().decode("utf-8", "replace")
            status = out.channel.recv_exit_status()
        except (OSError, EOFError, sftp._paramiko().SSHException) as exc:
            raise DockerError(f"Abfrage der Container fehlgeschlagen: {exc}") from exc
    finally:
        client.close()
    if status != 0:
        raise DockerError(describe_error(status, stderr))
    return parse_ps(stdout)


def exec_command(container: str, shell: str = "", user: str = "") -> str:
    """Befehl für den Host, der eine interaktive Shell im Container öffnet.

    ``shell`` leer = bash, falls im Container vorhanden, sonst sh. ``user`` leer = Standardbenutzer des Containers.
    """
    parts = ["docker", "exec", "-it"]
    if user.strip():
        parts += ["-u", user.strip()]
    parts.append(container)
    if shell.strip():
        try:
            parts += shlex.split(shell)
        except ValueError as exc:
            raise DockerError(f"Der Shell-Befehl ist ungültig: {exc}") from exc
    else:
        parts += ["sh", "-c", AUTO_SHELL]
    return shlex.join(parts)  # der Host ist POSIX


def container_target(
    conn: Connection, container: str, shell: str | None = None, user: str | None = None
) -> tuple[Connection, str]:
    """Verbindung (mit Containernamen im Titel) und Befehl, um in den Container zu gelangen.

    ``None`` für Shell/Benutzer = Standard der Verbindung; ein leerer Text heißt ausdrücklich „automatisch“ bzw. „Standard des Containers“.
    """
    command = exec_command(
        container,
        conn.docker_shell if shell is None else shell,
        conn.docker_user if user is None else user,
    )
    return replace(conn, name=f"{conn.name} · {container}"), command
