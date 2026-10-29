from __future__ import annotations

import uuid
from dataclasses import MISSING, dataclass, field, fields
from typing import Any


def new_id() -> str:
    return uuid.uuid4().hex


def _default_of(f):
    if f.default is not MISSING:
        return f.default
    if f.default_factory is not MISSING:
        return f.default_factory()
    return ""  # Pflichtfelder (name, host, command) sind Texte


def _from_dict(cls, data: dict[str, Any]):
    """Baut das Objekt aus einem Wörterbuch. Werte mit falschem Typ (``null``, Zahl statt Text …) fallen auf den Standard zurück,
    damit ein beschädigter Eintrag später nicht an jeder Stelle (Sortieren, ``.strip()``) einen Absturz auslöst."""
    if not isinstance(data, dict):
        raise TypeError(f"Objekt erwartet, {type(data).__name__} gefunden")
    fs = {f.name: f for f in fields(cls)}
    values = {}
    for k, v in data.items():
        f = fs.get(k)
        if f is None:
            continue
        kind = f.type
        if (kind == "str" and not isinstance(v, str)) or (kind == "bool" and not isinstance(v, bool)):
            v = _default_of(f)
        elif kind == "int" and (isinstance(v, bool) or not isinstance(v, int)):
            v = _default_of(f)
        values[k] = v
    return cls(**values)


COLUMN_KEYS = ("target", "terminal", "tags", "os")  # ausblendbare Spalten der Hauptansicht (der Name bleibt immer sichtbar)
DEFAULT_HIDDEN_COLUMNS = ("os",)
FONT_SCALE_MIN, FONT_SCALE_MAX = 70, 200  # erlaubter Bereich der Schriftgröße in Prozent


def _clean_tags(value) -> list[str]:
    """Beschädigte Werte (kein Listentyp, Nicht-Texte) ergeben eine saubere Liste statt eines Absturzes."""
    if not isinstance(value, list):
        return []
    seen: set[str] = set()
    out: list[str] = []
    for item in value:
        tag = " ".join(item.split()) if isinstance(item, str) else ""
        if tag and tag.casefold() not in seen:
            seen.add(tag.casefold())
            out.append(tag)
    return out


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
class Identity:
    """Wiederverwendbare Anmeldedaten: Benutzer, Schlüsseldatei und Passwort (im Schlüsselbund)."""

    name: str
    id: str = field(default_factory=new_id)
    user: str = ""
    identity_file: str = ""
    use_password: bool = False  # Passwort liegt im Schlüsselbund unter der Identitäts-ID
    notes: str = ""
    key_id: str = ""  # gespeicherter SSH-Schlüssel (SshKey); ersetzt dann die Schlüsseldatei

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Identity":
        return _from_dict(cls, data)


@dataclass
class SshKey:
    """Ein von Sesam verwalteter SSH-Schlüssel. Der private Teil liegt in einer Datei im Sesam-Ordner (nicht in der Konfiguration)
    und wird bei eingeschaltetem Passwort-Abgleich verschlüsselt mit synchronisiert."""

    name: str
    id: str = field(default_factory=new_id)
    type: str = ""  # "ed25519", "rsa", "ecdsa", ...
    public_key: str = ""  # eine Zeile im authorized_keys-Format, ohne Kommentar
    comment: str = ""
    fingerprint: str = ""  # SHA256:…
    encrypted: bool = False  # privater Schlüssel ist durch eine Passphrase geschützt
    created: str = ""  # Datum der Erzeugung bzw. des Imports (JJJJ-MM-TT)
    notes: str = ""
    use_password: bool = False  # der private Schlüssel liegt auf diesem Gerät (lokal abgeleitet, nicht Teil des Abgleichs)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SshKey":
        return _from_dict(cls, data)

    @property
    def authorized_line(self) -> str:
        """Zeile für authorized_keys (öffentlicher Schlüssel samt Kommentar)."""
        return f"{self.public_key} {self.comment}".strip()


SNIPPET_AFTER = ("shell", "wait", "close")  # nach dem Befehl: Shell offen lassen / auf Taste warten / Terminal schließen


@dataclass
class Snippet:
    """Ein gespeicherter Befehl, der auf einem oder mehreren Hosts im Terminal ausgeführt wird."""

    name: str
    id: str = field(default_factory=new_id)
    command: str = ""  # Shell-Befehl (auch mehrzeilig) für das Zielsystem
    category: str = ""  # Ordnerpfad mit „/“, leer = ohne Kategorie
    sudo: bool = False  # mit sudo ausführen (Benutzer und Passwort-Verhalten wie bei der Verbindung)
    after: str = "shell"
    notes: str = ""
    use_password: bool = False  # ohne Bedeutung, nur damit der Abgleich Snippets wie andere Einträge behandeln kann

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Snippet":
        s = _from_dict(cls, data)
        if s.after not in SNIPPET_AFTER:
            s.after = "shell"
        for name in ("name", "command", "category", "notes"):
            if not isinstance(getattr(s, name), str):
                setattr(s, name, "")
        s.sudo = s.sudo is True
        s.use_password = False
        return s


@dataclass
class Connection:
    name: str
    host: str
    id: str = field(default_factory=new_id)
    user: str = ""
    identity_id: str = ""  # gewählte Identität; ersetzt dann Benutzer, Schlüsseldatei und Passwort
    port: int = 22
    identity_file: str = ""
    use_password: bool = False  # Passwort liegt im Schlüsselbund des Betriebssystems
    jump_host: str = ""
    extra_args: str = ""
    remote_command: str = ""
    sudo_mode: str = ""  # "" = aus, "login" = sudo -i, "shell" = sudo -s
    sudo_user: str = ""  # Zielbenutzer, leer = root
    sudo_use_password: bool = True  # gespeichertes Passwort auch für sudo verwenden
    key_id: str = ""  # gespeicherter SSH-Schlüssel (SshKey); ersetzt dann die Schlüsseldatei
    docker: bool = False  # laufende Docker-Container des Hosts als Unterelemente im Baum anzeigen
    docker_shell: str = ""  # Shell im Container, leer = bash, sonst sh
    docker_user: str = ""  # Benutzer im Container (docker exec -u), leer = Standard des Containers
    proxmox: bool = False  # Gäste (VMs, LXC) des Proxmox-Hosts als Unterelemente im Baum anzeigen (ersetzt Docker)
    cisco: bool = False  # Ports eines Cisco-Geräts (IOS, IOS-XE, NX-OS) als Unterelemente im Baum anzeigen (ersetzt Docker/Proxmox)
    proxmox_vm_user: str = ""  # Benutzer für den SSH-Sprung in eine VM, leer = root
    group: str = ""
    terminal_id: str = ""  # leer = Standardterminal
    notes: str = ""
    os_info: str = ""  # Betriebssystem des Hosts, bei der ersten Verbindung ermittelt (siehe sesam.osinfo)
    os_checked: float = 0.0  # Zeitpunkt der letzten erfolgreichen Abfrage (Unix-Zeit); danach alle 7 Tage beim Verbinden erneuert
    color: str = ""  # Schlüssel aus sesam.colors.PALETTE, leer = keine Farbe
    tags: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Connection":
        conn = _from_dict(cls, data)
        if not 1 <= conn.port <= 65535:
            conn.port = 22
        if not isinstance(conn.color, str):
            conn.color = ""
        conn.tags = _clean_tags(conn.tags)
        if not isinstance(conn.os_info, str):
            conn.os_info = ""
        if isinstance(conn.os_checked, bool) or not isinstance(conn.os_checked, (int, float)):
            conn.os_checked = 0.0
        return conn


@dataclass
class SyncSettings:
    """Einstellungen für die Synchronisation über ein Git-Repository (nur lokal, nicht synchronisiert)."""

    enabled: bool = False
    url: str = ""
    branch: str = "main"
    auto: bool = True  # beim Start abgleichen und nach jeder Änderung
    passwords: bool = True  # Passwörter verschlüsselt mitsynchronisieren
    passphrase_saved: bool = False  # Sync-Passwort liegt im Schlüsselbund
    username: str = ""  # Benutzername für HTTPS-Repositories (leer: SSH-Schlüssel bzw. vorhandene Git-Zugangsdaten)
    git_password_saved: bool = False  # Passwort bzw. Token für das Repository liegt im Schlüsselbund
    last_sync: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SyncSettings":
        return _from_dict(cls, data)


@dataclass
class Config:
    terminals: list[Terminal] = field(default_factory=list)
    connections: list[Connection] = field(default_factory=list)
    identities: list[Identity] = field(default_factory=list)
    ssh_keys: list[SshKey] = field(default_factory=list)
    snippets: list[Snippet] = field(default_factory=list)
    default_terminal_id: str = ""
    sync: SyncSettings = field(default_factory=SyncSettings)
    theme: str = "system"  # "system", "light" oder "dark"
    language: str = "system"  # Oberflächensprache: "system", "de" oder "en" (nur lokal, wird nicht synchronisiert)
    font_scale: int = 100  # Schriftgröße der Oberfläche in Prozent (nur lokal, wird nicht synchronisiert)
    wireguard: bool = False  # WireGuard-Anzeige in der Statuszeile; aus = gar nicht abfragen und nicht anzeigen (nur lokal, wird nicht synchronisiert)
    column_widths: list[int] = field(default_factory=list)  # Spaltenbreiten der Hauptansicht
    hidden_columns: list[str] = field(default_factory=lambda: list(DEFAULT_HIDDEN_COLUMNS))  # ausgeblendete Spalten (nur lokal, wird nicht synchronisiert)
    window_size: list[int] = field(default_factory=list)  # [Breite, Höhe] des Hauptfensters beim Beenden (nur lokal)
    window_maximized: bool = False  # Hauptfenster war beim Beenden maximiert (nur lokal)
    groups: list[str] = field(default_factory=list)  # explizit angelegte Gruppen (auch leere)
    group_colors: dict[str, str] = field(default_factory=dict)  # Gruppenpfad -> Farbschlüssel (sesam.colors)
    collapsed_groups: list[str] = field(default_factory=list)  # zugeklappte Gruppen der Hauptansicht (nur lokal, wird nicht synchronisiert)
    version: int = 1

    def terminal_by_id(self, terminal_id: str) -> Terminal | None:
        return next((t for t in self.terminals if t.id == terminal_id), None)

    def snippet_by_id(self, snippet_id: str) -> Snippet | None:
        return next((s for s in self.snippets if s.id == snippet_id), None)

    def identity_by_id(self, identity_id: str) -> Identity | None:
        return next((i for i in self.identities if i.id == identity_id), None)

    def ssh_key_by_id(self, key_id: str) -> SshKey | None:
        return next((k for k in self.ssh_keys if k.id == key_id), None)

    def connection_by_id(self, connection_id: str) -> Connection | None:
        return next((c for c in self.connections if c.id == connection_id), None)
