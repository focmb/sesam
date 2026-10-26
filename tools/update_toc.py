#!/usr/bin/env python3
"""Aktualisiert das Inhaltsverzeichnis in README.md und README.en.md.

Das Verzeichnis wird aus den ``##``-Überschriften erzeugt (Codeblöcke werden
ignoriert) und vor dem ersten Abschnitt eingefügt. Ein vorhandenes
Verzeichnis wird ersetzt. Gibt die geänderten Dateien aus.

Aufruf: python tools/update_toc.py
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Datei -> (Titel des Verzeichnisses)
FILES = {
    "README.md": "Inhaltsverzeichnis",
    "README.en.md": "Table of contents",
}


def slug(text: str) -> str:
    """GitHub/Gitea-kompatibler Anker."""
    text = re.sub(r"[^\w\- ]", "", text.strip().lower())
    return text.replace(" ", "-")


def build(text: str, title: str) -> str:
    lines, skip = [], False
    for line in text.split("\n"):
        if line.startswith("## " + title):
            skip = True
            continue
        if skip and line.startswith("## "):
            skip = False
        if not skip:
            lines.append(line)

    heads, fence, first = [], False, None
    for n, line in enumerate(lines):
        if line.lstrip().startswith("```"):
            fence = not fence
        if not fence and line.startswith("## "):
            if first is None:
                first = n
            heads.append(line[3:].strip())
    if first is None:
        return text
    toc = ["## " + title, ""] + [f"- [{h}](#{slug(h)})" for h in heads] + [""]
    lines[first:first] = toc
    return "\n".join(lines)


def main() -> int:
    for name, title in FILES.items():
        path = ROOT / name
        if not path.exists():
            continue
        old = path.read_text(encoding="utf-8")
        new = build(old, title)
        if new != old:
            path.write_text(new, encoding="utf-8")
            print(name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
