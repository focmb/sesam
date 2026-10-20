from __future__ import annotations

import uuid
from dataclasses import dataclass, field, fields
from typing import Any


def new_id() -> str:
    return uuid.uuid4().hex


def _from_dict(cls, data: dict[str, Any]):
    names = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in data.items() if k in names})


@dataclass
class Terminal:
    """Ein Terminal-Programm, definiert über eine Befehlsvorlage.

    Platzhalter in ``command``:
      {ssh_args}     -> ssh-Befehl als einzelne Argumente (eigenes Token)
      {ssh_command}  -> kompletter ssh-Befehl als ein String
      {title} {host} {user} {port} {destination}
    """

    name: str
    command: str
    id: str = field(default_factory=new_id)
    platform: str = "all"  # "all", "linux", "windows"
    new_console: bool = False  # Windows: eigenes Konsolenfenster (cmd, powershell)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Terminal":
        return _from_dict(cls, data)


@dataclass
class Connection:
    name: str
    host: str
    id: str = field(default_factory=new_id)
    user: str = ""
    port: int = 22
    identity_file: str = ""
    use_password: bool = False  # Passwort liegt im Schlüsselbund des Betriebssystems
    jump_host: str = ""
    extra_args: str = ""
    remote_command: str = ""
    group: str = ""
    terminal_id: str = ""  # leer = Standardterminal
    notes: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Connection":
        return _from_dict(cls, data)


@dataclass
class Config:
    terminals: list[Terminal] = field(default_factory=list)
    connections: list[Connection] = field(default_factory=list)
    default_terminal_id: str = ""
    version: int = 1

    def terminal_by_id(self, terminal_id: str) -> Terminal | None:
        return next((t for t in self.terminals if t.id == terminal_id), None)

    def connection_by_id(self, connection_id: str) -> Connection | None:
        return next((c for c in self.connections if c.id == connection_id), None)

    def groups(self) -> list[str]:
        return sorted({c.group for c in self.connections if c.group}, key=str.lower)
