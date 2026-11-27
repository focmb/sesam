"""Import und Export eigener Farbschemas als JSON-Datei (reine Python-Logik, ohne GUI)."""
from __future__ import annotations

import json

from . import schemes
from .i18n import tr
from .models import ColorScheme, new_id

FORMAT = "sesam-colorscheme"
VERSION = 1
MAX_BYTES = 64 * 1024  # größere Dateien werden gar nicht erst gelesen


class SchemeFileError(ValueError):
    """Die Datei ist kein gültiges Farbschema; der Text ist für die Anzeige gedacht."""


def export_text(scheme: ColorScheme) -> str:
    data = {
        "format": FORMAT,
        "version": VERSION,
        "name": scheme.name,
        "base": scheme.base,
        "ui": {k: scheme.ui[k] for k in schemes.UI_KEYS},
        "terminal": {**{k: scheme.terminal[k] for k in schemes.TERMINAL_KEYS}, "ansi": list(scheme.terminal["ansi"])},
    }
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def _colors(section, keys, what: str) -> dict:
    if not isinstance(section, dict):
        raise SchemeFileError(tr("„{what}“ fehlt oder ist kein Objekt", what=what))
    for k in keys:
        if not schemes.is_hex(section.get(k)):
            raise SchemeFileError(tr("Ungültige Farbe bei {what}.{key}", what=what, key=k))
    return {k: section[k].lower() for k in keys}


def parse(raw: bytes | str, existing_names=()) -> ColorScheme:
    """Prüft strikt und liefert ein Schema mit neuer ID; ein vorhandener Name bekommt „ (2)“, „ (3)“ …"""
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    if len(raw) > MAX_BYTES:
        raise SchemeFileError(tr("Die Datei ist zu groß"))
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise SchemeFileError(tr("Die Datei ist kein gültiges JSON")) from exc
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise SchemeFileError(tr("Die Datei ist kein Sesam-Farbschema"))
    if data.get("version") != VERSION:
        raise SchemeFileError(tr("Nicht unterstützte Version des Farbschema-Formats"))
    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        raise SchemeFileError(tr("Der Name fehlt"))
    if data.get("base") not in ("light", "dark"):
        raise SchemeFileError(tr("„base“ muss „light“ oder „dark“ sein"))
    ui = _colors(data.get("ui"), schemes.UI_KEYS, "ui")
    term_in = data.get("terminal")
    terminal = _colors(term_in, schemes.TERMINAL_KEYS, "terminal")
    ansi = term_in.get("ansi")
    if not (isinstance(ansi, list) and len(ansi) == schemes.ANSI_COUNT and all(schemes.is_hex(c) for c in ansi)):
        raise SchemeFileError(tr("terminal.ansi muss {count} Farben enthalten", count=schemes.ANSI_COUNT))
    terminal["ansi"] = [c.lower() for c in ansi]
    return ColorScheme(name=unique_name(" ".join(name.split())[:schemes.NAME_MAX], existing_names), id=new_id(),
                       base=data["base"], ui=ui, terminal=terminal)


def unique_name(name: str, existing) -> str:
    taken = {n.casefold() for n in existing}
    if name.casefold() not in taken:
        return name
    n = 2
    while True:
        suffix = f" ({n})"
        candidate = name[:schemes.NAME_MAX - len(suffix)] + suffix
        if candidate.casefold() not in taken:
            return candidate
        n += 1
