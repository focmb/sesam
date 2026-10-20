from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

from .models import Config, Connection, Identity, Terminal
from .terminals import default_terminals


def config_dir() -> Path:
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "ssh_verwaltung"


def config_path() -> Path:
    return config_dir() / "config.json"


def load(path: Path | None = None) -> Config:
    path = path or config_path()
    if not path.exists():
        terminals = default_terminals()
        cfg = Config(terminals=terminals, default_terminal_id=terminals[0].id if terminals else "")
        return cfg
    data = json.loads(path.read_text(encoding="utf-8"))
    cfg = Config(
        terminals=[Terminal.from_dict(t) for t in data.get("terminals", [])],
        connections=[Connection.from_dict(c) for c in data.get("connections", [])],
        default_terminal_id=data.get("default_terminal_id", ""),
        groups=list(data.get("groups", [])),
        column_widths=[int(w) for w in data.get("column_widths", []) if isinstance(w, (int, float))],
        identities=[Identity.from_dict(i) for i in data.get("identities", [])],
        version=data.get("version", 1),
    )
    if not cfg.terminals:
        cfg.terminals = default_terminals()
    if not cfg.terminal_by_id(cfg.default_terminal_id) and cfg.terminals:
        cfg.default_terminal_id = cfg.terminals[0].id
    return cfg


def save(cfg: Config, path: Path | None = None) -> None:
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(cfg), indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
