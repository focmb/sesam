"""Tags: freie Schlagwörter an Verbindungen (mehrere je Verbindung, Groß-/Kleinschreibung egal)."""
from __future__ import annotations

from .models import Config


def parse(text: str) -> list[str]:
    """„prod, Kunde A; db“ -> ['prod', 'Kunde A', 'db']. Doppelte (ohne Rücksicht auf Groß-/Kleinschreibung) entfallen."""
    return clean(text.replace(";", ",").split(","))


def clean(values) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        tag = " ".join(str(value).split())
        if tag and tag.casefold() not in seen:
            seen.add(tag.casefold())
            out.append(tag)
    return out


def format(tags: list[str]) -> str:
    return ", ".join(tags)


def all_tags(cfg: Config) -> list[str]:
    """Alle vergebenen Tags, alphabetisch; bei abweichender Schreibweise gewinnt die häufigste."""
    counts: dict[str, dict[str, int]] = {}
    for conn in cfg.connections:
        for tag in conn.tags:
            variants = counts.setdefault(tag.casefold(), {})
            variants[tag] = variants.get(tag, 0) + 1
    return sorted((max(v, key=lambda t: (v[t], t)) for v in counts.values()), key=str.casefold)
