"""SFTP-Zugriff für den Dateibrowser: Verbindungsaufbau mit den Daten einer Sesam-Verbindung, Auflisten und Übertragen.

Alle Funktionen blockieren und gehören in einen Hintergrund-Thread. Zu jeder Verbindung gehört eine ``SftpSession``;
Übertragungen öffnen je einen eigenen SFTP-Kanal über dieselbe SSH-Verbindung, damit sie sich nicht gegenseitig
(und das Auflisten) ausbremsen.
"""
from __future__ import annotations

import os
import posixpath
import stat
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import launcher, passwords
from .models import Config, Connection
from .i18n import tr

CHUNK = 256 * 1024
CONNECT_TIMEOUT = 15


class SftpError(Exception):
    """Fehler mit einer für Anwender lesbaren Meldung."""


class SftpAuthError(SftpError):
    """Anmeldung abgelehnt (falsches oder fehlendes Passwort bzw. Schlüssel)."""


class TransferCancelled(Exception):
    pass


@dataclass(frozen=True)
class RemoteEntry:
    name: str
    is_dir: bool
    size: int
    mtime: int
    mode: int
    is_link: bool = False


def _paramiko():
    try:
        import paramiko
    except ImportError as exc:
        raise SftpError(tr("Das Paket „paramiko“ ist nicht installiert (pip install paramiko).")) from exc
    return paramiko


def default_known_hosts() -> Path:
    return Path.home() / ".ssh" / "known_hosts"


def _known_hosts_name(host: str, port: int) -> str:
    return host if port == 22 else f"[{host}]:{port}"


def _make_host_key_policy(known_hosts: Path):
    """Wie ``StrictHostKeyChecking=accept-new``: unbekannte Hosts werden gemerkt, geänderte Schlüssel abgelehnt.

    Die Datei wird nur um eine Zeile ergänzt (nie neu geschrieben), damit gehashte oder kommentierte Einträge
    des Anwenders unangetastet bleiben.
    """
    paramiko = _paramiko()

    class AcceptNew(paramiko.MissingHostKeyPolicy):
        def missing_host_key(self, client, hostname, key):
            line = f"{hostname} {key.get_name()} {key.get_base64()}\n"
            try:
                known_hosts.parent.mkdir(parents=True, exist_ok=True)
                needs_newline = known_hosts.exists() and known_hosts.stat().st_size > 0 and not known_hosts.read_bytes().endswith(b"\n")
                with open(known_hosts, "a", encoding="utf-8") as fh:
                    fh.write(("\n" if needs_newline else "") + line)
            except OSError:
                pass  # Verbindung trotzdem erlauben; der Schlüssel gilt dann nur für diese Sitzung
            client.get_host_keys().add(hostname, key.get_name(), key)

    return AcceptNew()


def open_client(
    cfg: Config,
    conn: Connection,
    password: str | None = None,
    known_hosts: Path | None = None,
    timeout: float = CONNECT_TIMEOUT,
    purpose: str | None = None,
):
    """Baut eine SSH-Verbindung mit den Daten einer Sesam-Verbindung auf (inkl. Identität und gespeichertem Passwort).

    ``password`` überschreibt das gespeicherte Passwort (z. B. nach einer Abfrage im Dialog).
    ``purpose`` erscheint in der Meldung, wenn die Verbindung einen Jump-Host nutzt (wird hier noch nicht unterstützt).
    Liefert ``(SSHClient, effektive Verbindung)``; der Aufrufer schließt den Client.
    """
    if purpose is None:
        purpose = tr("die Dateiübertragung")
    paramiko = _paramiko()
    eff, password_key = launcher.resolve(cfg, conn)
    try:
        launcher.check_key(cfg, eff)
    except launcher.LaunchError as exc:
        raise SftpError(str(exc)) from exc
    if eff.jump_host.strip():
        raise SftpError(tr("Für Verbindungen mit Jump-Host ist {purpose} noch nicht möglich.", purpose=purpose))
    if not eff.host.strip():
        raise SftpError(tr("Die Verbindung hat keinen Host."))
    if password is None and eff.use_password:
        try:
            password = passwords.get_password(password_key)
        except passwords.PasswordError as exc:
            raise SftpError(str(exc)) from exc

    client = paramiko.SSHClient()
    try:
        client.load_system_host_keys()
    except OSError:
        pass
    known_hosts = known_hosts or default_known_hosts()
    if known_hosts.exists():
        try:
            client.load_host_keys(str(known_hosts))
        except OSError:
            pass
    client.set_missing_host_key_policy(_make_host_key_policy(known_hosts))

    key_file = os.path.expanduser(eff.identity_file) if eff.identity_file else None
    # Mit Passwort und ohne Schlüsseldatei nur das Passwort versuchen: Jeder weitere Schlüssel aus ~/.ssh oder dem Agent zählt als
    # Anmeldeversuch, und Switches (Cisco: 3 Versuche) trennen die Verbindung dann, bevor das Passwort drankommt.
    password_only = bool(password) and not key_file
    try:
        client.connect(
            look_for_keys=not password_only,
            allow_agent=not password_only,
            hostname=eff.host.strip(),
            port=eff.port or 22,
            username=eff.user or None,
            password=password or None,
            key_filename=key_file,
            timeout=timeout,
            banner_timeout=timeout,
            auth_timeout=timeout,
        )
        return client, eff
    except paramiko.BadHostKeyException as exc:
        client.close()
        raise SftpError(
            tr("Der Schlüssel von {host} hat sich geändert (möglicher Angriff). Prüfe die Datei known_hosts, bevor du erneut verbindest.", host=eff.host)
        ) from exc
    except paramiko.AuthenticationException as exc:
        client.close()
        raise SftpAuthError(tr("Anmeldung abgelehnt (Passwort oder Schlüssel stimmen nicht).")) from exc
    except paramiko.PasswordRequiredException as exc:
        client.close()
        raise SftpAuthError(tr("Der Schlüssel ist durch eine Passphrase geschützt.")) from exc
    except (paramiko.SSHException, OSError, EOFError) as exc:
        client.close()
        if "No authentication methods" in str(exc):  # weder Passwort noch Schlüssel vorhanden
            raise SftpAuthError(tr("Es ist kein Passwort oder Schlüssel hinterlegt.")) from exc
        if "No existing session" in str(exc):  # die Gegenseite hat während der Anmeldung aufgelegt
            raise SftpError(tr("Verbindung zu {host} fehlgeschlagen: Das Gerät hat die Verbindung während der Anmeldung getrennt "
                               "(zu viele Anmeldeversuche oder nicht unterstützte Verfahren).", host=eff.host)) from exc
        raise SftpError(tr("Verbindung zu {host} fehlgeschlagen: {exc}", host=eff.host, exc=exc)) from exc


class SftpSession:
    """Eine geöffnete SSH-Verbindung mit einem SFTP-Kanal zum Durchsehen von Ordnern."""

    def __init__(self, client, label: str):
        self._client = client
        self.label = label
        self._browse = client.open_sftp()
        self._lock = threading.Lock()  # der Browse-Kanal wird nur einzeln benutzt

    # --- Verbindungsaufbau ---
    @classmethod
    def connect(
        cls,
        cfg: Config,
        conn: Connection,
        password: str | None = None,
        known_hosts: Path | None = None,
        timeout: float = CONNECT_TIMEOUT,
    ) -> "SftpSession":
        """Verbindet mit den Daten der Sesam-Verbindung (inkl. Identität und gespeichertem Passwort).

        ``password`` überschreibt das gespeicherte Passwort (z. B. nach einer Abfrage im Dialog).
        """
        client, eff = open_client(cfg, conn, password=password, known_hosts=known_hosts, timeout=timeout)
        try:
            return cls(client, eff.name or eff.host)
        except (_paramiko().SSHException, OSError, EOFError) as exc:  # z. B. SFTP-Subsystem nicht verfügbar
            client.close()
            raise SftpError(tr("Verbindung zu {host} fehlgeschlagen: {exc}", host=eff.host, exc=exc)) from exc

    def close(self) -> None:
        """Schließt die Verbindung; mehrfach oder bei schon getrennter Verbindung aufrufbar, ohne Fehler."""
        for closer in (self._browse.close, self._client.close):
            try:
                closer()
            except Exception:  # EOFError/OSError/SSHException: die Gegenseite ist ohnehin weg
                pass

    def new_channel(self):
        """Eigener SFTP-Kanal für eine Übertragung."""
        return self._client.open_sftp()

    # --- Ordner ---
    def home(self) -> str:
        with self._lock:
            return self._browse.normalize(".")

    def normalize(self, path: str) -> str:
        with self._lock:
            try:
                return self._browse.normalize(path)
            except OSError as exc:
                raise SftpError(tr("Pfad nicht gefunden: {path} ({exc})", path=path, exc=exc)) from exc

    def listdir(self, path: str) -> list[RemoteEntry]:
        with self._lock:
            try:
                attrs = self._browse.listdir_attr(path)
            except OSError as exc:
                raise SftpError(tr("Ordner kann nicht gelesen werden: {path} ({exc})", path=path, exc=exc)) from exc
            entries = []
            for a in attrs:
                mode = a.st_mode or 0
                is_link = stat.S_ISLNK(mode)
                is_dir = stat.S_ISDIR(mode)
                if is_link:  # Ziel des Links bestimmt, ob er sich wie ein Ordner öffnen lässt
                    try:
                        is_dir = stat.S_ISDIR(self._browse.stat(posixpath.join(path, a.filename)).st_mode or 0)
                    except OSError:
                        is_dir = False
                entries.append(RemoteEntry(a.filename, is_dir, a.st_size or 0, a.st_mtime or 0, mode, is_link))
        return entries

    def mkdir(self, path: str) -> None:
        with self._lock:
            try:
                self._browse.mkdir(path)
            except OSError as exc:
                raise SftpError(tr("Ordner konnte nicht angelegt werden: {exc}", exc=exc)) from exc

    def rename(self, old: str, new: str) -> None:
        with self._lock:
            try:
                self._browse.rename(old, new)
            except OSError as exc:
                raise SftpError(tr("Umbenennen fehlgeschlagen: {exc}", exc=exc)) from exc

    def remove(self, path: str) -> None:
        """Löscht eine Datei, einen Link oder einen Ordner samt Inhalt."""
        with self._lock:
            try:
                self._remove(path)
            except OSError as exc:
                raise SftpError(tr("Löschen fehlgeschlagen: {exc}", exc=exc)) from exc

    def _remove(self, path: str) -> None:
        mode = self._browse.lstat(path).st_mode or 0
        if stat.S_ISDIR(mode):  # Links auf Ordner sind keine Ordner (lstat): nur der Link wird entfernt
            for a in self._browse.listdir_attr(path):
                self._remove(posixpath.join(path, a.filename))
            self._browse.rmdir(path)
        else:
            self._browse.remove(path)

    # --- Übertragungen ---
    def upload(
        self,
        local_paths: list[str],
        remote_dir: str,
        progress: Callable[[int, int, str], None] | None = None,
        cancel: threading.Event | None = None,
        skip: set[str] | None = None,
    ) -> int:
        """Lädt Dateien und Ordner (rekursiv) in ``remote_dir``. ``skip`` = Namen oberster Ebene, die übergangen werden.

        Liefert die Anzahl übertragener Dateien. ``progress(erledigt, gesamt, aktuelle_datei)`` zählt Bytes.
        """
        plan = _plan_local([p for p in local_paths if os.path.basename(p.rstrip("/\\")) not in (skip or set())])
        total = sum(size for _, _, size in plan.files)
        done = 0
        sftp = self.new_channel()
        try:
            for rel_dir in plan.dirs:
                _check(cancel)
                target = posixpath.join(remote_dir, rel_dir)
                try:
                    sftp.mkdir(target)
                except OSError:
                    try:
                        if not stat.S_ISDIR(sftp.stat(target).st_mode or 0):
                            raise SftpError(tr("{target} existiert und ist kein Ordner.", target=target))
                    except OSError as exc:
                        raise SftpError(tr("Ordner konnte nicht angelegt werden: {target} ({exc})", target=target, exc=exc)) from exc
            for local, rel, size in plan.files:
                _check(cancel)
                target = posixpath.join(remote_dir, rel)
                base = done

                def cb(sent, _total, base=base, name=rel):
                    if progress:
                        progress(base + sent, total, name)
                    _check(cancel)

                try:
                    sftp.put(local, target, callback=cb)
                except TransferCancelled:
                    try:
                        sftp.remove(target)
                    except OSError:
                        pass
                    raise
                except OSError as exc:
                    raise SftpError(tr("Hochladen von {rel} fehlgeschlagen: {exc}", rel=rel, exc=exc)) from exc
                done += size
                if progress:
                    progress(done, total, rel)
            return len(plan.files)
        finally:
            sftp.close()

    def download(
        self,
        remote_paths: list[str],
        local_dir: str,
        progress: Callable[[int, int, str], None] | None = None,
        cancel: threading.Event | None = None,
        skip: set[str] | None = None,
    ) -> int:
        """Lädt Dateien und Ordner (rekursiv) nach ``local_dir`` herunter. Parameter wie bei ``upload``."""
        sftp = self.new_channel()
        try:
            try:
                plan = _plan_remote(sftp, [p for p in remote_paths if posixpath.basename(p.rstrip("/")) not in (skip or set())], cancel)
            except OSError as exc:
                raise SftpError(tr("Ordner kann nicht gelesen werden: {exc}", exc=exc)) from exc
            total = sum(size for _, _, size in plan.files)
            done = 0
            for rel_dir in plan.dirs:
                _check(cancel)
                try:
                    os.makedirs(os.path.join(local_dir, *rel_dir.split("/")), exist_ok=True)
                except OSError as exc:
                    raise SftpError(tr("Ordner konnte nicht angelegt werden: {rel_dir} ({exc})", rel_dir=rel_dir, exc=exc)) from exc
            for remote, rel, size in plan.files:
                _check(cancel)
                target = os.path.join(local_dir, *rel.split("/"))
                base = done

                def cb(got, _total, base=base, name=rel):
                    if progress:
                        progress(base + got, total, name)
                    _check(cancel)

                try:
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    sftp.get(remote, target, callback=cb)
                except TransferCancelled:
                    try:
                        os.remove(target)
                    except OSError:
                        pass
                    raise
                except OSError as exc:
                    raise SftpError(tr("Herunterladen von {rel} fehlgeschlagen: {exc}", rel=rel, exc=exc)) from exc
                done += size
                if progress:
                    progress(done, total, rel)
            return len(plan.files)
        finally:
            sftp.close()


# --- Planung ---------------------------------------------------------------------------------
@dataclass
class _Plan:
    dirs: list[str]  # relative Ordner (mit "/"), Eltern vor Kindern
    files: list[tuple[str, str, int]]  # (Quelle, relativer Zielpfad mit "/", Größe)


def _check(cancel: threading.Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise TransferCancelled()


def _plan_local(paths: list[str]) -> _Plan:
    plan = _Plan([], [])
    for raw in paths:
        path = raw.rstrip("/\\") or raw
        name = os.path.basename(path)
        if os.path.isdir(path) and not os.path.islink(path):
            _walk_local(path, name, plan)
        elif os.path.isfile(path):  # Links auf Dateien werden als Datei übertragen
            plan.files.append((path, name, os.path.getsize(path)))
    return plan


def _walk_local(path: str, rel: str, plan: _Plan) -> None:
    plan.dirs.append(rel)
    for entry in sorted(os.scandir(path), key=lambda e: e.name):
        child = f"{rel}/{entry.name}"
        if entry.is_dir(follow_symlinks=False):  # Links auf Ordner werden nicht verfolgt (Schleifen)
            _walk_local(entry.path, child, plan)
        elif entry.is_file():
            plan.files.append((entry.path, child, entry.stat().st_size))


def _plan_remote(sftp, paths: list[str], cancel: threading.Event | None) -> _Plan:
    plan = _Plan([], [])
    for raw in paths:
        path = raw.rstrip("/") or raw
        name = posixpath.basename(path)
        attr = sftp.lstat(path)
        mode = attr.st_mode or 0
        if stat.S_ISLNK(mode):
            attr = sftp.stat(path)  # Link auf eine Datei: Datei laden; Link auf einen Ordner wird übergangen
            mode = attr.st_mode or 0
            if stat.S_ISDIR(mode):
                continue
        if stat.S_ISDIR(mode):
            _walk_remote(sftp, path, name, plan, cancel)
        elif stat.S_ISREG(mode):
            plan.files.append((path, name, attr.st_size or 0))
    return plan


def _walk_remote(sftp, path: str, rel: str, plan: _Plan, cancel: threading.Event | None) -> None:
    _check(cancel)
    plan.dirs.append(rel)
    for a in sorted(sftp.listdir_attr(path), key=lambda x: x.filename):
        mode = a.st_mode or 0
        child, child_rel = posixpath.join(path, a.filename), f"{rel}/{a.filename}"
        if stat.S_ISDIR(mode):
            _walk_remote(sftp, child, child_rel, plan, cancel)
        elif stat.S_ISREG(mode):
            plan.files.append((child, child_rel, a.st_size or 0))
        elif stat.S_ISLNK(mode):
            try:
                target = sftp.stat(child)
            except OSError:
                continue
            if stat.S_ISREG(target.st_mode or 0):
                plan.files.append((child, child_rel, target.st_size or 0))
