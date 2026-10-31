"""Schnellstarter: unscharfe Suche über alle Verbindungen, gegliedert in Favoriten, Zuletzt verwendet und Alle."""
from __future__ import annotations

from dataclasses import dataclass, field

from .i18n import tr
from .models import Config, Connection

FAVORITES, RECENT, ALL = "favorites", "recent", "all"
SECTIONS = (FAVORITES, RECENT, ALL)
RECENT_SHOWN = 8  # ohne Suchbegriff: so viele zuletzt verwendete Verbindungen (ohne Favoriten)
_BOUNDARY = " -_./@:"


@dataclass
class Hit:
    conn: Connection
    section: str
    score: int = 0
    positions: list[int] = field(default_factory=list)  # Zeichen im Namen, die zur Suche passen (zum Hervorheben)
    used: float = 0.0  # Zeitpunkt der letzten Nutzung, 0 = nie


def match_term(term: str, text: str) -> tuple[int, list[int]] | None:
    """Passt ``term`` unscharf auf ``text``? Liefert (Punkte, Trefferpositionen) oder None.

    Ein zusammenhängender Treffer zählt mehr als verstreute Buchstaben, ein Treffer am Anfang oder nach einem
    Trenner (``-``, ``.``, ``@`` …) mehr als einer mitten im Wort."""
    q, t = term.casefold(), text.casefold()
    if not q:
        return 0, []
    i = t.find(q)
    if i >= 0:
        bonus = 200 if i == 0 else 100 if t[i - 1] in _BOUNDARY else 0
        return 1000 + bonus - 2 * i - (len(t) - len(q)), list(range(i, i + len(q)))
    positions: list[int] = []
    j = 0
    for k, ch in enumerate(t):
        if j < len(q) and ch == q[j]:
            positions.append(k)
            j += 1
    if j < len(q):
        return None
    span = positions[-1] - positions[0]
    return max(1, 400 - span - 2 * positions[0]), positions


def _fields(conn: Connection) -> tuple[tuple[str, int], ...]:
    """Durchsuchte Felder mit Gewicht; der Name steht immer an erster Stelle."""
    return ((conn.name, 3), (conn.host, 1), (conn.user, 1), (conn.group, 1), (" ".join(conn.tags), 1))


def score(conn: Connection, query: str) -> tuple[int, list[int]] | None:
    """Gesamtpunkte der Verbindung für die Suche: jedes Wort muss in mindestens einem Feld passen (Namen zählen dreifach)."""
    terms = query.split()
    total = 0
    positions: set[int] = set()
    for term in terms:
        best: tuple[int, list[int]] | None = None
        for index, (text, weight) in enumerate(_fields(conn)):
            m = match_term(term, text) if text else None
            if m is not None and (best is None or m[0] * weight > best[0]):
                best = (m[0] * weight, m[1] if index == 0 else [])
        if best is None:
            return None
        total += best[0]
        positions.update(best[1])
    return total, sorted(positions)


def search(cfg: Config, query: str = "") -> list[Hit]:
    """Alle passenden Verbindungen in Reihenfolge der Abschnitte: Favoriten, Zuletzt verwendet, Alle.

    Jede Verbindung steht nur einmal, im ersten passenden Abschnitt. Ohne Suchbegriff zeigt „Zuletzt verwendet“ nur
    die jüngsten ``RECENT_SHOWN``; mit Suchbegriff alle passenden zuvor verwendeten."""
    query = query.strip()
    hits: list[Hit] = []
    for conn in cfg.connections:
        m = score(conn, query) if query else (0, [])
        if m is None:
            continue
        used = cfg.recent.get(conn.id, 0.0)
        section = FAVORITES if conn.favorite else RECENT if used else ALL
        hits.append(Hit(conn, section, m[0], m[1], used))
    if not query:
        newest = sorted((h for h in hits if h.section == RECENT), key=lambda h: -h.used)
        for h in newest[RECENT_SHOWN:]:
            h.section = ALL
    out: list[Hit] = []
    for section in SECTIONS:
        group = [h for h in hits if h.section == section]
        if section == RECENT and not query:
            group.sort(key=lambda h: -h.used)
        else:
            group.sort(key=lambda h: (-h.score, h.conn.name.casefold()))
        out.extend(group)
    return out


def ago(seconds: float) -> str:
    """„vor 3 Min.“, „gestern“ … für die Zeit seit der letzten Nutzung."""
    seconds = max(0.0, seconds)
    if seconds < 60:
        return tr("gerade eben")
    if seconds < 3600:
        return tr("vor {n} Min.", n=int(seconds // 60))
    if seconds < 86400:
        return tr("vor {n} Std.", n=int(seconds // 3600))
    days = int(seconds // 86400)
    if days == 1:
        return tr("gestern")
    return tr("vor {n} Tagen", n=days)
