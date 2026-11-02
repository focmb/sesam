"""Zwischenablage-Verlauf: Text, den man in Sesam kopiert hat, bleibt abrufbar und lässt sich in mehrere Terminals einfügen.

Der Verlauf gehört nur zu diesem Gerät: Er liegt in einer eigenen Datei neben der Konfiguration und läuft nie durch die
Git-Synchronisation. Kopierter Text enthält oft Passwörter oder Tokens, deshalb wird die Datei verschlüsselt (AES-GCM);
der Schlüssel liegt im Schlüsselbund. Ohne Schlüsselbund bleibt der Verlauf im Arbeitsspeicher, es sei denn, der Nutzer erlaubt
ausdrücklich das unverschlüsselte Speichern.

Dieses Modul kennt kein Qt und ist deshalb ohne Oberfläche testbar.
"""
from __future__ import annotations

import base64
import binascii
import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Callable

from . import passwords
from .i18n import tr

MAX_ENTRY_BYTES = 1_000_000  # ein einzelner Eintrag (längere Texte werden nicht aufgenommen)
MAX_ENTRIES = 200
MAX_TOTAL_BYTES = 20_000_000  # ältere, nicht angeheftete Einträge fallen zuerst heraus
FILE_VERSION = 1
KEY_ID = "clipboard-key"  # Eintrag im Schlüsselbund
_AAD = b"sesam-clipboard-v1"
MAX_SLOTS = 9  # die ersten neun Einträge der Anzeige sind per Alt+Ziffer erreichbar


@dataclass
class Entry:
    text: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    created: float = field(default_factory=time.time)
    pinned: bool = False
    pinned_at: float = 0.0
    source: str = ""  # Name der Verbindung, aus deren Terminal kopiert wurde (leer: aus einem anderen Programm)

    @property
    def size(self) -> int:
        return len(self.text.encode("utf-8"))

    @property
    def lines(self) -> int:
        return self.text.count("\n") + (0 if not self.text or self.text.endswith("\n") else 1)

    def first_line(self, width: int = 100) -> str:
        """Erste nicht leere Zeile, gekürzt; Tabulatoren werden zu Leerzeichen."""
        for line in self.text.splitlines():
            line = line.strip().replace("\t", " ")
            if line:
                return line if len(line) <= width else line[: width - 1] + "…"
        return ""


@dataclass
class Settings:
    enabled: bool = True
    persist: bool = True  # den Verlauf über einen Neustart behalten
    clear_on_exit: bool = False  # beim Beenden leeren (Angeheftetes bleibt)
    allow_plaintext: bool = False  # ohne Schlüsselbund unverschlüsselt speichern


def human_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / 1024 / 1024:.1f} MB"


class KeyStore:
    """Schlüssel für die Verschlüsselung der Datei."""

    def get(self) -> bytes | None:
        raise NotImplementedError

    def create(self) -> bytes | None:
        """Erzeugt und speichert einen neuen Schlüssel (der alte ist damit unbrauchbar); ``None``, wenn das nicht geht."""
        raise NotImplementedError


class KeyringKeys(KeyStore):
    def get(self) -> bytes | None:
        try:
            value = passwords.get_password(KEY_ID)
        except passwords.PasswordError:
            return None
        if not value:
            return None
        try:
            key = base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError):
            return None
        return key if len(key) == 32 else None

    def create(self) -> bytes | None:
        key = os.urandom(32)
        try:
            passwords.set_password(KEY_ID, base64.b64encode(key).decode("ascii"))
        except passwords.PasswordError:
            return None
        return key if self.get() == key else None  # manche Backends melden Erfolg, speichern aber nichts


class History:
    def __init__(self, path: Path, keys: KeyStore | None = None):
        self.path = Path(path)
        self.keys = keys or KeyringKeys()
        self.settings = Settings()
        self.entries: list[Entry] = []  # neueste zuerst
        self.listeners: list[Callable[[], None]] = []
        self.notice = ""  # Hinweis für die Oberfläche (Speichern nicht möglich, Datei defekt …)
        self.locked = False  # Datei ist verschlüsselt, der Schlüssel aber nicht verfügbar: nichts überschreiben
        self._key: bytes | None = None
        self._rotate = False

    # --- Einträge ---
    def _changed(self) -> None:
        for listener in list(self.listeners):
            listener()

    def settings_changed(self) -> None:
        self._changed()

    @staticmethod
    def too_large(text: str) -> bool:
        return len(text) > MAX_ENTRY_BYTES or len(text.encode("utf-8")) > MAX_ENTRY_BYTES

    def add(self, text: str, source: str = "") -> Entry | None:
        """Nimmt Text auf. Leere und zu große Texte werden abgelehnt (``None``); ein vorhandener Eintrag rückt nach oben."""
        if not self.settings.enabled or not text or not text.strip() or self.too_large(text):
            return None
        for entry in self.entries:
            if entry.text == text:
                entry.created = time.time()
                if source:
                    entry.source = source
                self.entries.remove(entry)
                self.entries.insert(0, entry)
                self._changed()
                return entry
        entry = Entry(text, source=source)
        self.entries.insert(0, entry)
        self._trim()
        self._changed()
        return entry

    def _trim(self) -> None:
        total = sum(e.size for e in self.entries)
        while len(self.entries) > MAX_ENTRIES or total > MAX_TOTAL_BYTES:
            victim = next((e for e in reversed(self.entries) if not e.pinned), None)
            if victim is None:
                break
            self.entries.remove(victim)
            total -= victim.size

    def get(self, entry_id: str) -> Entry | None:
        return next((e for e in self.entries if e.id == entry_id), None)

    def remove(self, entry_id: str) -> None:
        entry = self.get(entry_id)
        if entry is not None:
            self.entries.remove(entry)
            self._changed()

    def set_pinned(self, entry_id: str, pinned: bool) -> None:
        entry = self.get(entry_id)
        if entry is not None and entry.pinned != pinned:
            entry.pinned = pinned
            entry.pinned_at = time.time() if pinned else 0.0
            self._changed()

    def clear(self, include_pinned: bool = False) -> None:
        """Leert den Verlauf. Mit ``include_pinned`` auch das Angeheftete; dann wird der Schlüssel erneuert, sodass Reste
        der alten Datei auf dem Datenträger nicht mehr lesbar sind."""
        self.entries = [] if include_pinned else [e for e in self.entries if e.pinned]
        if include_pinned:
            self._rotate = True
        self._changed()

    def ordered(self) -> list[Entry]:
        """Reihenfolge der Anzeige: zuerst das Angeheftete (in der Reihenfolge des Anheftens), dann der Rest, neueste zuerst."""
        pinned = sorted((e for e in self.entries if e.pinned), key=lambda e: e.pinned_at)
        return pinned + [e for e in self.entries if not e.pinned]

    def search(self, query: str) -> list[Entry]:
        words = query.lower().split()
        if not words:
            return self.ordered()
        return [e for e in self.ordered() if all(w in e.text.lower() or w in e.source.lower() for w in words)]

    # --- Speichern ---
    def _get_key(self, create: bool) -> bytes | None:
        if self._key is None:
            self._key = self.keys.get()
        if self._key is None and create:
            self._key = self.keys.create()
        return self._key

    def _encrypt(self, key: bytes, payload: bytes) -> str:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        nonce = os.urandom(12)
        return base64.b64encode(nonce + AESGCM(key).encrypt(nonce, payload, _AAD)).decode("ascii")

    @staticmethod
    def _decrypt(key: bytes, blob: str) -> bytes:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        raw = base64.b64decode(blob, validate=True)
        return AESGCM(key).decrypt(raw[:12], raw[12:], _AAD)

    def save(self) -> bool:
        """Schreibt die Datei. ``False``, wenn der Verlauf nur im Arbeitsspeicher bleibt (Hinweis in ``notice``)."""
        if self.locked:  # sonst würden wir den noch unlesbaren Verlauf überschreiben
            return False
        doc: dict = {"version": FILE_VERSION, "settings": asdict(self.settings), "encrypted": False, "entries": []}
        stored = False
        self.notice = ""
        if self._rotate:  # nach „Alles leeren“: alter Schlüssel ungültig machen
            self._rotate = False
            self._key = self.keys.create()
        if self.settings.persist and self.entries:
            items = [asdict(e) for e in self.entries]
            key = self._get_key(create=True)
            if key is not None:
                doc["encrypted"] = True
                doc.pop("entries")
                doc["data"] = self._encrypt(key, json.dumps(items, ensure_ascii=False).encode("utf-8"))
                stored = True
            elif self.settings.allow_plaintext:
                doc["entries"] = items
                stored = True
            else:
                self.notice = tr("Kein Schlüsselbund verfügbar: Der Verlauf wird nicht gespeichert. In den Optionen lässt sich das unverschlüsselte Speichern erlauben.")
        try:
            self._write(json.dumps(doc, ensure_ascii=False))
        except OSError as exc:
            self.notice = tr("Der Verlauf konnte nicht gespeichert werden: {exc}", exc=exc)
            return False
        return stored or not self.entries or not self.settings.persist

    def _write(self, text: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.path.parent.chmod(0o700)
        except OSError:
            pass
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, self.path)
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    # --- Laden ---
    def load(self) -> None:
        self.entries = []
        self.locked = False
        self._read(merge=False)

    def unlock(self) -> bool:
        """Zweiter Versuch, wenn der Schlüsselbund beim Start gesperrt war; bisherige Einträge im Arbeitsspeicher bleiben erhalten."""
        if not self.locked:
            return True
        self._key = None
        self.locked = False
        self._read(merge=True)
        self._changed()
        return not self.locked

    def _read(self, merge: bool) -> None:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return
        except OSError as exc:
            self.notice = tr("Der Verlauf konnte nicht gelesen werden: {exc}", exc=exc)
            return
        try:
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("kein Objekt")
            self._apply_settings(data.get("settings"))
            if data.get("encrypted"):
                key = self._get_key(create=False)
                if key is None:
                    self.locked = True
                    self.notice = tr("Der gespeicherte Verlauf ist verschlüsselt, der Schlüssel aus dem Schlüsselbund ist nicht verfügbar. Der Verlauf bleibt unverändert und wird angezeigt, sobald der Schlüsselbund entsperrt ist.")
                    return
                items = json.loads(self._decrypt(key, data["data"]).decode("utf-8"))
            else:
                items = data.get("entries", [])
            loaded = [e for e in (self._entry_from(i) for i in items) if e is not None]
        except Exception as exc:  # noqa: BLE001 – eine beschädigte Datei darf den Start nie verhindern
            self._quarantine()
            self.notice = tr("Der gespeicherte Verlauf war nicht lesbar ({kind}) und wurde als „{name}“ beiseitegelegt.", kind=type(exc).__name__, name=self.path.name + ".defekt")
            return
        if merge:
            known = {e.text for e in self.entries}
            self.entries.extend(e for e in loaded if e.text not in known)
        else:
            self.entries = loaded
        self.notice = ""

    def _quarantine(self) -> None:
        try:
            os.replace(self.path, self.path.with_name(self.path.name + ".defekt"))
        except OSError:
            pass

    def _apply_settings(self, data) -> None:
        if not isinstance(data, dict):
            return
        for f in fields(Settings):
            if isinstance(data.get(f.name), bool):
                setattr(self.settings, f.name, data[f.name])

    @staticmethod
    def _entry_from(item) -> Entry | None:
        if not isinstance(item, dict) or not isinstance(item.get("text"), str) or not item["text"]:
            return None
        entry = Entry(item["text"])
        if isinstance(item.get("id"), str) and item["id"]:
            entry.id = item["id"][:32]
        for name in ("created", "pinned_at"):
            if isinstance(item.get(name), (int, float)) and not isinstance(item.get(name), bool):
                setattr(entry, name, float(item[name]))
        entry.pinned = item.get("pinned") is True
        if isinstance(item.get("source"), str):
            entry.source = item["source"][:200]
        return entry
