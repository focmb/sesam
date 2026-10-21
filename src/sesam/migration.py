"""Übernahme der Daten aus der früheren Namensgebung („ssh_verwaltung“) nach „sesam“."""
from __future__ import annotations

from typing import Callable

from . import passwords, storage
from .syncing import GIT_PASSWORD_KEY, PASSPHRASE_KEY

LEGACY_NAME = "ssh_verwaltung"
LEGACY_SERVICE = "ssh_verwaltung"
MARKER = "legacy-migrated"


class ServiceStore:
    """Zugriff auf einen Dienstnamen im Schlüsselbund."""

    def __init__(self, service: str):
        self.service = service

    def get(self, key: str) -> str | None:
        return passwords._keyring().get_password(self.service, key)

    def set(self, key: str, value: str) -> None:
        passwords._keyring().set_password(self.service, key, value)

    def delete(self, key: str) -> None:
        kr = passwords._keyring()
        try:
            kr.delete_password(self.service, key)
        except kr.errors.PasswordDeleteError:
            pass


def migrate_config_dir() -> bool:
    """Benennt den alten Konfigurationsordner um, falls der neue noch nicht existiert. True = verschoben."""
    new = storage.config_dir()
    old = new.parent / LEGACY_NAME
    if old.is_dir() and not new.exists():
        old.rename(new)
        return True
    return False


def migrate_passwords(cfg, old=None, new=None) -> int:
    """Kopiert Passwörter (Verbindungen, Identitäten, Sync-Passwort) in den neuen Schlüsselbund-Dienst.

    Der alte Eintrag wird erst nach erfolgreicher Prüfung der Kopie gelöscht. Liefert die Anzahl übernommener Einträge.
    """
    old = old or ServiceStore(LEGACY_SERVICE)
    new = new or ServiceStore(passwords.SERVICE)
    keys = [o.id for o in list(cfg.connections) + list(cfg.identities) if o.use_password]
    keys.extend([PASSPHRASE_KEY, GIT_PASSWORD_KEY])
    moved = 0
    for key in keys:
        value = old.get(key)
        if value is None:
            continue
        if new.get(key) is None:
            new.set(key, value)
        if new.get(key) is not None:
            old.delete(key)
            moved += 1
    return moved


def run(old=None, new=None, log: Callable[[str], None] = lambda _m: None) -> None:
    """Wird beim Start aufgerufen. Fehler (z. B. gesperrter Schlüsselbund) werden verschluckt und beim nächsten Start erneut versucht."""
    moved_dir = migrate_config_dir()
    marker = storage.config_dir() / MARKER
    if marker.exists():
        return
    try:
        cfg = storage.load()
        n = migrate_passwords(cfg, old, new)
        if moved_dir or n:
            log(f"Aus früherer Version übernommen: Konfiguration verschoben={moved_dir}, Passwörter={n}")
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("ok\n", encoding="utf-8")
    except Exception as exc:  # noqa: BLE001 - Start darf daran nicht scheitern
        log(f"Übernahme der Passwörter später erneut versuchen: {exc}")
