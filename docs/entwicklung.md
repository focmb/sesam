# Entwicklung: Bauen, Builds und Tests

← [Zur README](../README.md) · *[English version](en/entwicklung.md)*

## Programm bauen (Linux und Windows)

Mit PyInstaller entsteht ein Programmordner, der ohne installiertes Python läuft. Gebaut wird auf dem jeweiligen Zielsystem
(Linux auf Linux, Windows auf Windows; Cross-Kompilieren geht nicht):

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e . pyinstaller
python packaging/build.py
```

Ergebnis in `dist/`: der Ordner `sesam/` und ein Archiv (`sesam-<Version>-linux-x86_64.tar.gz` bzw. `…-windows-x86_64.zip`).
- **Linux:** `sesam/sesam` starten. Zielsysteme brauchen die üblichen Qt-Bibliotheken, bei Ubuntu/Debian z. B. `libxcb-cursor0` und `libegl1`
  (`QT_DEBUG_PLUGINS=1 ./sesam` zeigt, was fehlt). Gebaut wird gegen die glibc des Build-Systems; für möglichst viele Systeme auf einer älteren
  Distribution bauen (getestet: Ubuntu 22.04).
- **Debian/Ubuntu-Paket:** `python packaging/build_deb.py` (nach `build.py`) erzeugt `dist/sesam_<Version>_amd64.deb`. Installieren mit
  `sudo apt install ./sesam_<Version>_amd64.deb`: Das Programm liegt in `/opt/sesam`, der Befehl `sesam` in `/usr/bin`, dazu Starter und Icon
  für das Anwendungsmenü; `apt` zieht die nötigen Qt-Bibliotheken, `openssh-client` und `git` nach. Entfernen mit `sudo apt remove sesam`
  (die Konfiguration in `~/.config/sesam` bleibt). Das Paket entsteht auf Debian/Ubuntu (braucht `dpkg-deb`); für möglichst viele Systeme
  auf Ubuntu 22.04 bauen.
- **Arch Linux / Manjaro:** `packaging/aur/PKGBUILD` baut Sesam als Pacman-Paket aus dem Quelltext-Tarball des Tags (GitHub-Spiegel
  `github.com/focmb/sesam`). Bauen und installieren mit `cd packaging/aur && makepkg -si`; `-s` holt fehlende Abhängigkeiten
  (`pyside6`, `python-paramiko` usw.), `-i` installiert das Paket. Entfernen mit `sudo pacman -R sesam` (die Konfiguration in
  `~/.config/sesam` bleibt). Das Paket ist bewusst nicht im AUR veröffentlicht. Bei einer neuen Version: Tag setzen und pushen,
  in `PKGBUILD` `pkgver` anpassen, `updpkgsums` ausführen und `makepkg --printsrcinfo > .SRCINFO` erneuern.
- **Release-Dateien:** Prüfsummen für den Release: `cd dist && sha256sum *.tar.gz *.deb > SHA256SUMS`.
- **Windows:** `sesam\sesam.exe` starten (Fenster ohne Konsole). Der Ordner enthält zusätzlich `sesam-cli.exe`; der ssh-Wrapper (Passwort/sudo)
  braucht eine Konsole und nutzt diese Variante automatisch. Voraussetzungen: OpenSSH-Client (Windows-Feature) und Git. Unsignierte Programme
  löst SmartScreen u. U. eine Warnung aus.
  Das Icon der `.exe` liegt als `packaging/windows/sesam.ico` im Repository; bei geändertem Programm-Icon neu erzeugen mit `python packaging/make_icon.py`.
- Es ist bewusst keine Ein-Datei-Variante: der Wrapper startet Sesam bei jeder Verbindung erneut, Entpacken würde jedes Mal dauern.

## Automatischer Build (GitHub Actions)

Gebaut wird nur im GitHub-Spiegel (`github.com/focmb/sesam`); Gitea selbst hat keine Actions. `.github/workflows/build.yml` nutzt GitHub-Runner, eigene Runner sind nicht nötig.
Der Workflow läuft bei jedem Push auf `main`, bei jedem Tag `v*` und auf Knopfdruck (*Actions → Build → Run workflow*):

- **Linux** (Ubuntu 22.04): Tests, dann `sesam-<Version>-linux-x86_64.tar.gz` und `sesam_<Version>_amd64.deb`.
- **Windows:** `sesam-<Version>-windows-x86_64.zip` mit `sesam\sesam.exe` (mit Tests).
- **Release:** Bei einem Tag legt der Workflow auf GitHub ein Release mit allen Dateien und `SHA256SUMS` an. Bei „Run workflow“ liegen
  die Dateien nur als Artefakte (`sesam-linux`, `sesam-windows`) am Lauf.

Ablauf einer neuen Version: Version in `pyproject.toml` und `src/sesam/__init__.py` erhöhen, committen, `git tag -a v<Version> -m "Version <Version>"`
und `git push origin main --follow-tags`. Der Gitea-Spiegel überträgt Commits und Tag nach GitHub, dort startet der Build.

## Tests

```bash
PYTHONPATH=src python -m unittest discover -s tests      # oder: python -m pytest
```

Die GUI-Tests laufen ohne Bildschirm (`QT_QPA_PLATFORM=offscreen` wird gesetzt) und werden übersprungen, wenn PySide6 oder die
Qt-Systembibliotheken fehlen. Die Sync-Tests brauchen `git`.
Die Paket-Tests (`test_packaging.py`) brauchen `dpkg-deb` und werden sonst übersprungen.
Die SFTP-Tests (`test_sftp.py`, `test_sftp_gui.py`) starten dafür einen kleinen SSH/SFTP-Server im Testprozess (nur auf `127.0.0.1`, mit
`paramiko`) und werden übersprungen, wenn `paramiko` fehlt. Dasselbe gilt für die Docker-Tests (`test_docker.py`, `test_docker_gui.py`); der Testserver
antwortet dort auf `docker ps`. Ebenso `test_proxmox.py` (antwortet auf `pvesh`) und `test_cisco.py` (simulierter Switch mit Shell).
