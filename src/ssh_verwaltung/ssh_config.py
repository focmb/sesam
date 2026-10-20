from __future__ import annotations

from pathlib import Path

from .models import Connection


def parse_ssh_config(path: Path | None = None) -> list[Connection]:
    """Liest Hosts aus einer OpenSSH-Konfiguration (ohne Wildcard-Einträge)."""
    path = path or Path.home() / ".ssh" / "config"
    if not path.exists():
        return []
    entries: list[dict] = []
    current: list[dict] = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.replace("=", " ", 1).split(None, 1)
        if len(parts) != 2:
            continue
        key, value = parts[0].lower(), parts[1].strip().strip('"')
        if key == "host":
            aliases = [a for a in value.split() if not any(c in a for c in "*?!")]
            current = [{"alias": alias} for alias in aliases]
            entries.extend(current)
        else:
            for entry in current:
                entry.setdefault(key, value)
    result = []
    for e in entries:
        try:
            port = int(e.get("port", 22))
        except ValueError:
            port = 22
        result.append(
            Connection(
                name=e["alias"],
                host=e.get("hostname", e["alias"]),
                user=e.get("user", ""),
                port=port,
                identity_file=e.get("identityfile", ""),
                jump_host=e.get("proxyjump", ""),
                group="ssh-config",
            )
        )
    return result
