from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from .models import FONT_SCALE_MAX, FONT_SCALE_MIN, Config, Connection, Identity, SshKey, Snippet, SyncSettings, Terminal
from .terminals import default_terminals


class ConfigError(Exception):
    """Die Konfigurationsdatei existiert, ist aber unbrauchbar (kein gültiges JSON oder falsche Struktur)."""

    def __init__(self, path: Path, reason: str):
        super().__init__(f"{path}: {reason}")
        self.path = path
        self.reason = reason


def config_dir() -> Path:
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "sesam"


def config_path() -> Path:
    return config_dir() / "config.json"


def load(path: Path | None = None) -> Config:
    path = path or config_path()
    if not path.exists():
        terminals = default_terminals()
        cfg = Config(terminals=terminals, default_terminal_id=terminals[0].id if terminals else "")
        return cfg
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("die Datei enthält kein JSON-Objekt")
        cfg = _from_data(data)
    except json.JSONDecodeError as exc:  # vor ValueError, da eine Unterklasse davon
        raise ConfigError(path, f"kein gültiges JSON (Zeile {exc.lineno}, Spalte {exc.colno}: {exc.msg})") from exc
    except UnicodeDecodeError as exc:
        raise ConfigError(path, f"keine gültige UTF-8-Datei ({exc.reason})") from exc
    except (TypeError, KeyError, ValueError, AttributeError) as exc:
        raise ConfigError(path, f"unerwarteter Inhalt ({type(exc).__name__}: {exc})") from exc
    if not cfg.terminals:
        cfg.terminals = default_terminals()
    if not cfg.terminal_by_id(cfg.default_terminal_id) and cfg.terminals:
        cfg.default_terminal_id = cfg.terminals[0].id
    return cfg


def quarantine(path: Path) -> Path:
    """Legt eine unbrauchbare Konfigurationsdatei beiseite (sie wird nie gelöscht) und liefert den neuen Pfad."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = path.with_name(f"{path.name}.defekt-{stamp}")
    n = 1
    while target.exists():
        n += 1
        target = path.with_name(f"{path.name}.defekt-{stamp}-{n}")
    os.replace(path, target)
    return target


def _font_scale(value) -> int:
    """Reiner Anzeigewert: Unbrauchbares ergibt 100 %, Außergewöhnliches wird auf den erlaubten Bereich begrenzt."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:
        return 100
    return max(FONT_SCALE_MIN, min(FONT_SCALE_MAX, int(value)))


def _group_colors(value) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {k: v for k, v in value.items() if isinstance(k, str) and k and isinstance(v, str) and v}


def _window_size(value) -> list[int]:
    """[Breite, Höhe] oder leer; ein beschädigter Wert darf den Start nie verhindern."""
    if (isinstance(value, list) and len(value) == 2 and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value)
            and all(300 <= v <= 20000 for v in value)):
        return [int(value[0]), int(value[1])]
    return []


def _from_data(data: dict) -> Config:
    return Config(
        terminals=[Terminal.from_dict(t) for t in data.get("terminals", [])],
        connections=[Connection.from_dict(c) for c in data.get("connections", [])],
        default_terminal_id=data.get("default_terminal_id", ""),
        groups=list(data.get("groups", [])),
        group_colors=_group_colors(data.get("group_colors")),
        # reiner Anzeigezustand: ein beschädigter Wert darf den Start nie verhindern
        collapsed_groups=[g for g in data["collapsed_groups"] if isinstance(g, str)] if isinstance(data.get("collapsed_groups"), list) else [],
        sync=SyncSettings.from_dict(data.get("sync", {})),
        theme=data.get("theme", "system") if data.get("theme") in ("system", "light", "dark") else "system",
        font_scale=_font_scale(data.get("font_scale")),
        window_size=_window_size(data.get("window_size")),
        window_maximized=data.get("window_maximized") is True,
        column_widths=[int(w) for w in data.get("column_widths", []) if isinstance(w, (int, float))],
        identities=[Identity.from_dict(i) for i in data.get("identities", [])],
        snippets=[Snippet.from_dict(s) for s in data.get("snippets", []) if isinstance(s, dict)] if isinstance(data.get("snippets"), list) else [],
        ssh_keys=[SshKey.from_dict(k) for k in data.get("ssh_keys", []) if isinstance(k, dict)],
        version=data.get("version", 1),
    )


def save(cfg: Config, path: Path | None = None) -> None:
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(cfg), indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
