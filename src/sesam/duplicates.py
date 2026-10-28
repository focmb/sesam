"""Doppelte Einträge erkennen: dieselbe Verbindung (bzw. Identität) mit verschiedenen IDs.

Das entsteht, wenn auf mehreren Geräten dasselbe unabhängig angelegt wird, z. B. durch den Import aus ``~/.ssh/config``
(jeder Import vergibt neue IDs). Der Abgleich ordnet Einträge nur über die ID zu und würde beide behalten.
"""
from __future__ import annotations

from typing import Callable, Mapping

from .models import Config, Connection


def signature(kind: str, data: Mapping) -> tuple | None:
    """Merkmale, an denen zwei Einträge als „derselbe“ gelten. ``None``: für diese Art wird nicht zusammengeführt."""
    if kind == "c":
        return (str(data.get("name", "")), str(data.get("host", "")).strip().lower(), int(data.get("port") or 22),
                str(data.get("user", "")), str(data.get("group", "")))
    if kind == "i":
        return (str(data.get("name", "")), str(data.get("user", "")))
    return None


def find_duplicate_connections(cfg: Config) -> list[list[Connection]]:
    """Gruppen gleicher Verbindungen (Name, Host, Port, Benutzer, Gruppe). Der erste Eintrag jeder Gruppe ist der, der bleibt:
    bevorzugt einer mit gespeichertem Passwort, sonst der mit der kleinsten ID."""
    groups: dict[tuple, list[Connection]] = {}
    for c in cfg.connections:
        groups.setdefault(signature("c", vars(c)), []).append(c)
    found = [sorted(g, key=lambda c: (not c.use_password, c.id)) for g in groups.values() if len(g) > 1]
    return sorted(found, key=lambda g: (g[0].name.lower(), g[0].host.lower()))


def remove_duplicates(cfg: Config, groups: list[list[Connection]], delete_password: Callable[[str], None]) -> tuple[list[Connection], list[str]]:
    """Entfernt in jeder Gruppe alle außer dem ersten Eintrag. Liefert (entfernte Verbindungen, Fehlermeldungen beim Löschen der Passwörter).

    Hat nur ein zu entfernender Eintrag ein Passwort und der bleibende keins, wird das Passwort nicht übertragen:
    ``find_duplicate_connections`` stellt solche Einträge bereits an die erste Stelle."""
    removed: list[Connection] = []
    failures: list[str] = []
    for group in groups:
        for conn in group[1:]:
            if conn.use_password:
                try:
                    delete_password(conn.id)
                except Exception as exc:  # noqa: BLE001 - Schlüsselbund-Fehler werden gemeldet, das Entfernen läuft weiter
                    failures.append(f"{conn.name}: {exc}")
            if conn in cfg.connections:
                cfg.connections.remove(conn)
                removed.append(conn)
    return removed, failures
