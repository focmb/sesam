"""Sprachumschaltung: Die Quelltexte sind deutsch, ``tr()`` liefert bei Bedarf die englische Fassung.

Der deutsche Originaltext dient als Schlüssel (siehe ``translations_en``). Fehlt eine Übersetzung, bleibt der
deutsche Text stehen – die Oberfläche bleibt dadurch immer benutzbar. Platzhalter ``{name}`` werden über
Schlüsselwörter eingesetzt: ``tr("Verbindung {name} löschen?", name=...)``.
"""
from __future__ import annotations

import json
import locale
import os

LANGUAGES = ("system", "de", "en")
LANGUAGE_NAMES = {"de": "Deutsch", "en": "English"}  # jede Sprache in ihrer eigenen Sprache

_language = "de"
_catalog: dict[str, str] | None = None


def normalize(value) -> str:
    """Unbrauchbare Werte ergeben „system“."""
    return value if value in LANGUAGES else "system"


def system_language() -> str:
    """„de“, wenn die Oberflächensprache des Systems Deutsch ist, sonst „en“."""
    candidates = [os.environ.get(v, "") for v in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG")]
    try:
        candidates.append(locale.getlocale()[0] or "")
    except (ValueError, TypeError):  # noqa: PERF203
        pass
    try:
        from PySide6.QtCore import QLocale

        candidates.append(QLocale.system().name())
    except Exception:  # noqa: BLE001 – ohne Qt (z. B. connect_cli) reichen die Umgebungsvariablen
        pass
    for value in candidates:
        value = value.strip().lower()
        if value and value not in ("c", "posix"):
            return "de" if value.startswith("de") else "en"
    return "en"


def resolve(setting: str) -> str:
    setting = normalize(setting)
    return system_language() if setting == "system" else setting


def set_language(setting: str) -> str:
    """Stellt die Sprache ein (``system``, ``de`` oder ``en``) und liefert die tatsächlich genutzte."""
    global _language, _catalog
    _language = resolve(setting)
    _catalog = None
    return _language


def language() -> str:
    return _language


def load_saved_language() -> str:
    """Liest nur die Spracheinstellung aus der Konfiguration (für Programmteile ohne GUI, z. B. ``--connect``)."""
    from . import storage

    try:
        data = json.loads(storage.config_path().read_text(encoding="utf-8"))
        setting = data.get("language") if isinstance(data, dict) else None
    except (OSError, ValueError):
        setting = None
    return set_language(setting)


def tr(text: str, /, **values) -> str:
    """Übersetzt ``text`` (deutscher Originaltext) in die eingestellte Sprache und setzt Platzhalter ein."""
    global _catalog
    if _language != "de":
        if _catalog is None:
            from .translations_en import EN

            _catalog = EN
        text = _catalog.get(text, text)
    return text.format(**values) if values else text
