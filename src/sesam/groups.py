"""Gruppenlogik: Gruppen sind Pfade mit „/“ (z. B. „Kunde A/Produktion“) und können verschachtelt werden."""
from __future__ import annotations

from .models import Config, Connection

SEP = "/"


def normalize(path: str) -> str:
    parts = [p.strip() for p in path.replace("\\", SEP).split(SEP)]
    return SEP.join(p for p in parts if p)


def parent_of(path: str) -> str:
    return path.rpartition(SEP)[0]


def name_of(path: str) -> str:
    return path.rpartition(SEP)[2]


def join(parent: str, name: str) -> str:
    return normalize(f"{parent}{SEP}{name}")


def ancestors(path: str) -> list[str]:
    """Alle Pfade von oben nach unten inklusive path selbst: 'A/B' -> ['A', 'A/B']."""
    parts = path.split(SEP) if path else []
    return [SEP.join(parts[: i + 1]) for i in range(len(parts))]


def all_groups(cfg: Config) -> list[str]:
    found: set[str] = set()
    for g in list(cfg.groups) + [c.group for c in cfg.connections]:
        found.update(ancestors(normalize(g)))
    return sorted(found, key=str.lower)


def _remap(cfg: Config, fn) -> None:
    for c in cfg.connections:
        c.group = fn(c.group)
    seen: list[str] = []
    for g in cfg.groups:
        g = fn(g)
        if g and g not in seen:
            seen.append(g)
    cfg.groups = seen


def add_group(cfg: Config, path: str) -> str:
    path = normalize(path)
    if not path:
        raise ValueError("Der Gruppenname darf nicht leer sein.")
    if path not in all_groups(cfg):
        cfg.groups.append(path)
    return path


def rename_group(cfg: Config, old: str, new: str) -> str:
    """Benennt eine Gruppe um bzw. verschiebt sie (inklusive Untergruppen und Verbindungen)."""
    new = normalize(new)
    if not new:
        raise ValueError("Der Gruppenname darf nicht leer sein.")
    if new == old:
        return new
    if new.startswith(old + SEP):
        raise ValueError("Eine Gruppe kann nicht in sich selbst verschoben werden.")

    def fn(g: str) -> str:
        if g == old:
            return new
        if g.startswith(old + SEP):
            return new + g[len(old):]
        return g

    _remap(cfg, fn)
    if new not in cfg.groups:
        cfg.groups.append(new)
    return new


def delete_group(cfg: Config, path: str) -> None:
    """Löscht die Gruppe; Verbindungen und Untergruppen rücken in die übergeordnete Gruppe."""
    parent = parent_of(path)

    def fn(g: str) -> str:
        if g == path:
            return parent
        if g.startswith(path + SEP):
            return join(parent, g[len(path) + 1:])
        return g

    _remap(cfg, fn)


def connections_in(cfg: Config, path: str, recursive: bool = True) -> list[Connection]:
    return [c for c in cfg.connections if c.group == path or (recursive and c.group.startswith(path + SEP))]
