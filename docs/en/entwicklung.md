# Development: building, builds and tests

← [Back to the README](../../README.en.md) · *[Deutsche Fassung](../entwicklung.md)*

## Building the program (Linux and Windows)

PyInstaller produces a program folder that runs without an installed Python. You build on the respective target system
(Linux on Linux, Windows on Windows; cross-compiling does not work):

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e . pyinstaller
python packaging/build.py
```

Result in `dist/`: the folder `sesam/` and an archive (`sesam-<version>-linux-x86_64.tar.gz` or `…-windows-x86_64.zip`).
- **Linux:** start `sesam/sesam`. Target systems need the usual Qt libraries, on Ubuntu/Debian e.g. `libxcb-cursor0` and `libegl1`
  (`QT_DEBUG_PLUGINS=1 ./sesam` shows what is missing). It is built against the glibc of the build system; to cover as many systems as possible, build on an older
  distribution (tested: Ubuntu 22.04).
- **Debian/Ubuntu package:** `python packaging/build_deb.py` (after `build.py`) creates `dist/sesam_<version>_amd64.deb`. Install with
  `sudo apt install ./sesam_<version>_amd64.deb`: the program goes to `/opt/sesam`, the `sesam` command to `/usr/bin`, plus launcher and icon
  for the application menu; `apt` pulls in the required Qt libraries, `openssh-client` and `git`. Remove with `sudo apt remove sesam`
  (the configuration in `~/.config/sesam` stays). The package is built on Debian/Ubuntu (needs `dpkg-deb`); to cover as many systems as possible,
  build on Ubuntu 22.04.
- **Arch Linux / Manjaro:** `packaging/aur/PKGBUILD` builds Sesam as a pacman package from the source tarball of the tag (GitHub mirror
  `github.com/focmb/sesam`). Build and install with `cd packaging/aur && makepkg -si`; `-s` fetches missing dependencies
  (`pyside6`, `python-paramiko`, etc.), `-i` installs the package. Remove with `sudo pacman -R sesam` (the configuration in
  `~/.config/sesam` stays). The package is deliberately not published in the AUR. For a new version: set and push the tag,
  adjust `pkgver` in `PKGBUILD`, run `updpkgsums` and regenerate `.SRCINFO` with `makepkg --printsrcinfo > .SRCINFO`.
- **Release files:** checksums for the release: `cd dist && sha256sum *.tar.gz *.deb > SHA256SUMS`.
- **Windows:** start `sesam\sesam.exe` (window without console). The folder additionally contains `sesam-cli.exe`; the ssh wrapper (password/sudo)
  needs a console and uses this variant automatically. Requirements: OpenSSH client (Windows feature) and Git. For unsigned programs
  SmartScreen may show a warning.
  The `.exe` icon is in the repository as `packaging/windows/sesam.ico`; if the program icon changes, regenerate it with `python packaging/make_icon.py`.
- It is deliberately not a single-file variant: the wrapper starts Sesam again for every connection, and unpacking would take time each time.

## Automatic build (Gitea Actions)

`.gitea/workflows/build.yml` runs on every tag `v*` (e.g. `git tag v1.2.0 && git push --tags`) and on demand
(*Actions → Build → Run workflow*). The Linux job installs the Qt system libraries, runs the tests and builds with
`packaging/build.py` and `packaging/build_deb.py`; `.tar.gz`, `.deb` and `SHA256SUMS` are attached to the run as the artifact `sesam-linux`
(from there they can be downloaded and attached to a release).

This requires a Gitea runner (`act_runner`, e.g. as a container next to Gitea) with the label `ubuntu-22.04`,
for example `ubuntu-22.04:docker://catthehacker/ubuntu:act-22.04`. The Windows job is commented out in the workflow until a Windows runner
(label `windows`, with Python and Git) exists; until then Windows is built by hand (see above).

## Automatic build (GitHub Actions)

In the GitHub mirror (`github.com/focmb/sesam`) `.github/workflows/build.yml` builds with GitHub runners; no runners of your own are needed.
The workflow runs on every push to `main`, on every tag `v*` and on demand (*Actions → Build → Run workflow*):

- **Linux** (Ubuntu 22.04): tests, then `sesam-<version>-linux-x86_64.tar.gz` and `sesam_<version>_amd64.deb`.
- **Windows:** `sesam-<version>-windows-x86_64.zip` with `sesam\sesam.exe` (with tests).
- **Release:** for a tag the workflow creates a GitHub release with all files and `SHA256SUMS`. With “Run workflow” the
  files are only attached to the run as artifacts (`sesam-linux`, `sesam-windows`).

Process for a new version: raise the version in `pyproject.toml` and `src/sesam/__init__.py`, commit, `git tag -a v<version> -m "Version <version>"`
and `git push origin main --follow-tags`. The Gitea mirror transfers commits and tag to GitHub, where the build starts.
The workflow in `.gitea/workflows/` remains independent of that.

## Tests

```bash
PYTHONPATH=src python -m unittest discover -s tests      # or: python -m pytest
```

The GUI tests run without a screen (`QT_QPA_PLATFORM=offscreen` is set) and are skipped if PySide6 or the
Qt system libraries are missing. The sync tests need `git`.
The package tests (`test_packaging.py`) need `dpkg-deb` and are skipped otherwise.
The SFTP tests (`test_sftp.py`, `test_sftp_gui.py`) start a small SSH/SFTP server inside the test process (only on `127.0.0.1`, using
`paramiko`) and are skipped if `paramiko` is missing. The same applies to the Docker tests (`test_docker.py`, `test_docker_gui.py`); the test server
answers `docker ps` there. Likewise `test_proxmox.py` (answers `pvesh`) and `test_cisco.py` (simulated switch with a shell).
