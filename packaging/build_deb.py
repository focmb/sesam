#!/usr/bin/env python3
"""Packt das gebaute Programm (dist/sesam, siehe packaging/build.py) als Debian-Paket.

Aufruf (auf Debian/Ubuntu, nach packaging/build.py):
    python packaging/build_deb.py

Ergebnis: dist/sesam_<Version>_<Architektur>.deb
Inhalt:   /opt/sesam/             das Programm (PyInstaller-Ordner)
          /usr/bin/sesam          Verweis darauf (Befehl „sesam“, auch für den ssh-Wrapper)
          /usr/share/applications/sesam.desktop und das Icon für das Anwendungsmenü
Ein Archiv-Release (.tar.gz) und das Paket enthalten dasselbe Programm; die Konfiguration (~/.config/sesam) bleibt bei Updates und Deinstallation unberührt.
"""
from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from sesam import __version__  # noqa: E402

LINUX = ROOT / "packaging" / "linux"
ARCH = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}

# Qt (xcb-Plugin) und das Programm selbst brauchen diese Systembibliotheken; PySide6 bringt Qt, aber nicht die X11-/GL-Anbindung mit.
DEPENDS = [
    "libc6 (>= 2.35)", "libegl1", "libgl1", "libxkbcommon0", "libxkbcommon-x11-0", "libdbus-1-3", "libfontconfig1",
    "libglib2.0-0", "libxcb-cursor0", "libxcb-icccm4", "libxcb-image0", "libxcb-keysyms1", "libxcb-render-util0",
    "libxcb-xinerama0", "libxcb-shape0", "openssh-client", "git",
]


def control(version: str, arch: str, size_kib: int) -> str:
    return (
        f"Package: sesam\n"
        f"Version: {version}\n"
        f"Section: net\n"
        f"Priority: optional\n"
        f"Architecture: {arch}\n"
        f"Installed-Size: {size_kib}\n"
        f"Depends: {', '.join(DEPENDS)}\n"
        f"Recommends: gnome-terminal | konsole | xfce4-terminal | xterm\n"
        f"Maintainer: Christian Leitzke <christian.leitzke@gmail.com>\n"
        f"Description: Verwaltung von SSH-Verbindungen\n"
        f" Sesam verwaltet SSH-Verbindungen in Gruppen, öffnet sie im Terminal Ihrer Wahl,\n"
        f" bietet einen SFTP-Dateibrowser und Docker-Container als Verbindungsziele\n"
        f" und gleicht die Konfiguration über ein Git-Repository ab.\n"
    )


def normalize_modes(root: Path) -> None:
    """Verzeichnisse 755, ausführbare Dateien 755, alle anderen 644 (unabhängig von umask und Quelle)."""
    for path in [root, *root.rglob("*")]:
        if path.is_symlink():
            continue
        if path.is_dir():
            path.chmod(0o755)
        else:
            path.chmod(0o755 if os.access(path, os.X_OK) or path.stat().st_mode & 0o111 else 0o644)


def build(folder: Path, out_dir: Path, version: str = __version__, arch: str | None = None) -> Path:
    arch = arch or ARCH.get(platform.machine().lower())
    if arch is None:
        raise SystemExit(f"Unbekannte Architektur: {platform.machine()}")
    if not (folder / "sesam").is_file():
        raise SystemExit(f"Kein gebautes Programm in {folder} gefunden. Erst bauen: python packaging/build.py")
    if shutil.which("dpkg-deb") is None:
        raise SystemExit("dpkg-deb fehlt (Paket „dpkg“). Das .deb lässt sich nur auf Debian/Ubuntu bauen.")
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"sesam_{version}_{arch}.deb"
    with tempfile.TemporaryDirectory() as tmp:
        pkg = Path(tmp) / "pkg"
        shutil.copytree(folder, pkg / "opt" / "sesam", symlinks=True)
        (pkg / "usr" / "bin").mkdir(parents=True)
        (pkg / "usr" / "bin" / "sesam").symlink_to("/opt/sesam/sesam")
        apps = pkg / "usr" / "share" / "applications"
        apps.mkdir(parents=True)
        (apps / "sesam.desktop").write_text(
            (LINUX / "sesam.desktop").read_text(encoding="utf-8").replace('"@EXEC@"', "sesam"), encoding="utf-8")
        icons = pkg / "usr" / "share" / "icons" / "hicolor" / "scalable" / "apps"
        icons.mkdir(parents=True)
        shutil.copy2(LINUX / "sesam.svg", icons / "sesam.svg")
        normalize_modes(pkg)
        size_kib = sum(p.stat().st_size for p in pkg.rglob("*") if p.is_file() and not p.is_symlink()) // 1024 + 1
        debian = pkg / "DEBIAN"
        debian.mkdir()
        (debian / "control").write_text(control(version, arch, size_kib), encoding="utf-8")
        debian.chmod(0o755)
        target.unlink(missing_ok=True)
        subprocess.run(["dpkg-deb", "--root-owner-group", "-Zxz", "--build", str(pkg), str(target)], check=True,
                       stdout=subprocess.DEVNULL)
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--folder", type=Path, default=ROOT / "dist" / "sesam", help="gebauter Programmordner")
    parser.add_argument("--out", type=Path, default=ROOT / "dist", help="Zielordner für das .deb")
    args = parser.parse_args()
    deb = build(args.folder, args.out)
    print(f"\nPaket: {deb} ({deb.stat().st_size / 1e6:.0f} MB)\nInstallieren: sudo apt install ./{deb.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
