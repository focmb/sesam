"""Snippets: gespeicherte Befehle, die im Terminal auf einem oder mehreren Hosts laufen."""
from __future__ import annotations

import shlex
from dataclasses import dataclass, field

from . import groups, launcher
from .models import Config, Connection, Snippet, Terminal

MAX_HOSTS_WITHOUT_WARNING = 1  # ab zwei Zielen fragt die Oberfläche vorher nach


def remote_command(snippet: Snippet) -> str:
    """Zeile für das Zielsystem (POSIX-Shell). Der Befehl läuft in ``sh -c``; sudo gilt nur für ihn, nicht für die Shell danach."""
    command = snippet.command.strip()
    line = f"sh -c {shlex.quote(command)}"
    if snippet.after == "wait":
        line += "; printf '\\n[Fertig. Eine Taste drücken zum Schließen] '; read -r _ </dev/tty"
    elif snippet.after == "shell":
        line += '; exec "${SHELL:-sh}" -l'
    return line


def categories(cfg: Config) -> list[str]:
    """Alle vorkommenden Kategorien samt Oberkategorien, sortiert."""
    found: set[str] = set()
    for s in cfg.snippets:
        cat = groups.normalize(s.category)
        if cat:
            found.update(groups.ancestors(cat))
            found.add(cat)
    return sorted(found, key=str.lower)


def in_category(cfg: Config, category: str) -> list[Snippet]:
    """Snippets genau dieser Kategorie ("" = ohne Kategorie), nach Name sortiert."""
    category = groups.normalize(category)
    return sorted((s for s in cfg.snippets if groups.normalize(s.category) == category), key=lambda s: s.name.lower())


@dataclass
class RunReport:
    started: list[str] = field(default_factory=list)  # Namen der Verbindungen mit gestartetem Terminal
    errors: list[tuple[str, str]] = field(default_factory=list)  # (Verbindung, Fehlertext)


def run(cfg: Config, snippet: Snippet, conns: list[Connection], terminal: Terminal | None = None) -> RunReport:
    """Startet je Verbindung ein Terminal mit dem Snippet. Ein Fehler bei einem Ziel stoppt die anderen nicht."""
    report = RunReport()
    if not snippet.command.strip():
        report.errors.append(("", "Das Snippet enthält keinen Befehl."))
        return report
    line = remote_command(snippet)
    for conn in conns:
        try:
            launcher.launch(cfg, conn, terminal, remote_command=line, sudo=snippet.sudo)
        except launcher.LaunchError as exc:
            report.errors.append((conn.name, str(exc)))
        else:
            report.started.append(conn.name)
    return report
