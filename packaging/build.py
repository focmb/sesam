#!/usr/bin/env python3
"""Baut Sesam als Programmordner (PyInstaller) und packt ihn als Archiv nach dist/.

Aufruf (im Projektordner, in einer Umgebung mit installierten Abhängigkeiten):
    pip install -e . pyinstaller
    python packaging/build.py

Ergebnis:  dist/sesam/                       (lauffähiger Ordner)
           dist/sesam-<Version>-linux-x86_64.tar.gz  bzw.  dist/sesam-<Version>-windows-x86_64.zip
Das Programm muss auf dem Zielsystem gebaut werden (Linux auf Linux, Windows auf Windows).
"""
from __future__ import annotations

import platform
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from sesam import __version__  # noqa: E402

DIST = ROOT / "dist"
WORK = ROOT / "build"


def main() -> int:
    is_win = sys.platform.startswith("win")
    system = "windows" if is_win else "linux"
    arch = platform.machine().lower().replace("amd64", "x86_64")
    for path in (DIST / "sesam", WORK):
        shutil.rmtree(path, ignore_errors=True)
    subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
         "--distpath", str(DIST), "--workpath", str(WORK), str(ROOT / "packaging" / "sesam.spec")],
        check=True,
    )
    folder = DIST / "sesam"
    name = f"sesam-{__version__}-{system}-{arch}"
    if is_win:
        archive = DIST / f"{name}.zip"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in sorted(folder.rglob("*")):
                zf.write(f, Path("sesam") / f.relative_to(folder))
    else:
        archive = DIST / f"{name}.tar.gz"
        with tarfile.open(archive, "w:gz") as tf:
            tf.add(folder, arcname="sesam")
    size = archive.stat().st_size / 1e6
    print(f"\nFertig: {folder}\nArchiv: {archive} ({size:.0f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
