# -*- mode: python ; coding: utf-8 -*-
# PyInstaller-Spezifikation für Sesam (Verzeichnis-Variante, kein Ein-Datei-Programm:
# der ssh-Wrapper startet Sesam bei jeder Verbindung erneut, Entpacken würde jedes Mal dauern).
#
# Windows: zwei Programme im selben Ordner
#   sesam.exe      Fensterprogramm ohne Konsole
#   sesam-cli.exe  Konsolenprogramm für den ssh-Wrapper und askpass (braucht eine Konsole)
# Linux: ein Programm "sesam" (der Wrapper läuft im Terminal und hat dort ohnehin eine Konsole).
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata

ROOT = Path(SPECPATH).parent
IS_WIN = sys.platform.startswith("win")
ICON = str(ROOT / "packaging" / "windows" / "sesam.ico") if IS_WIN else None  # Programm-Icon der .exe (erzeugt von packaging/make_icon.py)

datas, binaries, hiddenimports = [], [], []
# keyring findet seine Backends über Entry-Points (Paket-Metadaten) -> vollständig einsammeln
for pkg in ("keyring",):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h
datas += copy_metadata("keyring", recursive=True)
hiddenimports += collect_submodules("keyring.backends")
if not IS_WIN:
    for pkg in ("secretstorage", "jeepney"):
        hiddenimports += collect_submodules(pkg)
        datas += copy_metadata(pkg)
hiddenimports += ["cryptography.hazmat.primitives.ciphers.aead"]
# paramiko (SFTP-Dateibrowser) wird erst beim Öffnen des Fensters importiert
hiddenimports += collect_submodules("paramiko")

a = Analysis(
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(ROOT / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "pytest", "unittest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exes = [
    EXE(pyz, a.scripts, [], exclude_binaries=True, name="sesam", console=not IS_WIN, disable_windowed_traceback=False, icon=ICON),
]
if IS_WIN:
    exes.append(EXE(pyz, a.scripts, [], exclude_binaries=True, name="sesam-cli", console=True, icon=ICON))

coll = COLLECT(*exes, a.binaries, a.datas, strip=False, upx=False, name="sesam")
