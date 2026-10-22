"""Speicher für die privaten Teile der von Sesam verwalteten SSH-Schlüssel: je Schlüssel eine Datei im Sesam-Ordner.

Eine Datei statt Schlüsselbund, weil ``ssh -i`` eine Datei braucht und der Windows-Anmeldeinformationsspeicher nur sehr kleine
Einträge fasst (ein RSA-Schlüssel passt nicht hinein). Die Dateien sind nur für den Benutzer lesbar (0600, Ordner 0700).
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from . import storage
from .passwords import PasswordError
from .i18n import tr

_SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")


def directory() -> Path:
    return storage.config_dir() / "keys"


def _check(key_id: str) -> str:
    if not isinstance(key_id, str) or not _SAFE_ID.fullmatch(key_id):
        raise PasswordError(tr("Ungültige Schlüssel-ID: {key_id!r}", key_id=key_id))  # schützt vor Pfaden aus einem manipulierten Sync-Repository
    return key_id


def path_of(key_id: str) -> Path:
    return directory() / f"{_check(key_id)}.key"


class FileStore:
    """Gleiche Schnittstelle wie der Passwortspeicher (get/set/delete), damit der Abgleich beide gleich behandelt."""

    def get(self, key_id: str) -> str | None:
        path = path_of(key_id)
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise PasswordError(tr("Schlüsseldatei konnte nicht gelesen werden: {exc}", exc=exc)) from exc

    def set(self, key_id: str, value: str) -> None:
        path = path_of(key_id)
        text = value if value.endswith("\n") else value + "\n"  # OpenSSH verlangt den Zeilenumbruch am Ende
        tmp = path.with_suffix(".tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                path.parent.chmod(0o700)
            except OSError:
                pass
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
            os.replace(tmp, path)
            try:
                path.chmod(0o600)
            except OSError:
                pass
        except OSError as exc:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise PasswordError(tr("Schlüsseldatei konnte nicht gespeichert werden: {exc}", exc=exc)) from exc

    def delete(self, key_id: str) -> None:
        try:
            path_of(key_id).unlink(missing_ok=True)
        except OSError as exc:
            raise PasswordError(tr("Schlüsseldatei konnte nicht gelöscht werden: {exc}", exc=exc)) from exc

    def exists(self, key_id: str) -> bool:
        try:
            return path_of(key_id).is_file()
        except PasswordError:
            return False
