from __future__ import annotations

from .i18n import tr

SERVICE = "sesam"


class PasswordError(Exception):
    pass


def _keyring():
    try:
        import keyring
    except ImportError as exc:
        raise PasswordError(tr("Das Paket „keyring“ ist nicht installiert (pip install keyring).")) from exc
    return keyring


def get_password(connection_id: str) -> str | None:
    kr = _keyring()
    try:
        return kr.get_password(SERVICE, connection_id)
    except Exception as exc:  # Backend-spezifische Fehler
        raise PasswordError(tr("Schlüsselbund nicht verfügbar: {exc}", exc=exc)) from exc


def set_password(connection_id: str, password: str) -> None:
    kr = _keyring()
    try:
        kr.set_password(SERVICE, connection_id, password)
    except Exception as exc:
        raise PasswordError(tr("Passwort konnte nicht im Schlüsselbund gespeichert werden: {exc}", exc=exc)) from exc


def delete_password(connection_id: str) -> None:
    kr = _keyring()
    try:
        kr.delete_password(SERVICE, connection_id)
    except kr.errors.PasswordDeleteError:
        pass  # war nicht vorhanden
    except Exception as exc:
        raise PasswordError(tr("Passwort konnte nicht gelöscht werden: {exc}", exc=exc)) from exc


def apply_change(item, change: str | None) -> None:
    """Wendet eine Passwortänderung auf Verbindung oder Identität an.

    change: None = unverändert, "" = löschen, sonst neues Passwort.
    """
    if change is None:
        return
    if change:
        set_password(item.id, change)
        item.use_password = True
    else:
        delete_password(item.id)
        item.use_password = False
