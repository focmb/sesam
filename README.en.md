[![Build](https://github.com/focmb/sesam/actions/workflows/build.yml/badge.svg?branch=main)](https://github.com/focmb/sesam/actions/workflows/build.yml)
[![Release](https://img.shields.io/github/v/release/focmb/sesam)](https://github.com/focmb/sesam/releases/latest)
[![License: MIT](https://img.shields.io/github/license/focmb/sesam)](LICENSE)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![Platforms](https://img.shields.io/badge/platform-Linux%20%7C%20Windows-lightgrey)

<p align="center"><img src="packaging/linux/sesam.svg" alt="Sesam icon" width="128" height="128"></p>

# Sesam

SSH connection manager (Windows and Linux, Python + PySide6) for managing SSH connections.
Connections open in a **freely configurable terminal**.

*[Deutsche Fassung: README.md](README.md)*

## Table of contents

- [Features](#features)
- [Getting started](#getting-started)
- [Screenshots](#screenshots)
- [Appearance](#appearance)
- [Installing in the application menu / dash (Linux)](#installing-in-the-application-menu--dash-linux)
- [Groups](#groups)
- [Colors and tags](#colors-and-tags)
- [Configuring terminals](#configuring-terminals)
- [Identities](#identities)
- [SSH keys](#ssh-keys)
- [sudo](#sudo)
- [Passwords](#passwords)
- [Snippets](#snippets)
- [Transferring files (SFTP)](#transferring-files-sftp)
- [Docker containers](#docker-containers)
- [Proxmox](#proxmox)
- [Cisco devices](#cisco-devices)
- [WireGuard indicator](#wireguard-indicator)
- [Synchronization via Git](#synchronization-via-git)
- [Data](#data)
- [Building the program (Linux and Windows)](#building-the-program-linux-and-windows)
- [Automatic build (Gitea Actions)](#automatic-build-gitea-actions)
- [Automatic build (GitHub Actions)](#automatic-build-github-actions)
- [Tests](#tests)

## Features

- **Connection management:** SSH connections in nested groups with drag & drop, colors, tags, search and multi-select; import from `~/.ssh/config`.
- **Your own terminal:** Every connection opens in the terminal of your choice (Windows Terminal, PowerShell, cmd, PuTTY, GNOME Terminal, custom programs) through freely configurable command templates, also different per connection.
- **Credentials:** Passwords live in the operating system’s keychain, not in the configuration file. Identities bundle user, key and password for many connections.
- **SSH keys in the app:** Generate or import Ed25519/RSA keys, view fingerprints, install them on servers with one click (like `ssh-copy-id`) and assign them to connections.
- **sudo:** Switch straight to root or another user after login, optionally with the stored password and without typing it again.
- **Snippets:** Run saved commands (with categories) on one or several hosts at once, optionally with sudo, then keep the shell open, wait for a key or close the terminal.
- **File transfer (SFTP):** Two-pane file browser with drag & drop, background progress, rename, delete and new folder; no `sftp` or `scp` needed on your computer.
- **Docker:** Show a host’s containers in the connection tree; start, stop, restart and delete them, and open a shell in a container with a double-click.
- **Proxmox:** VMs and LXC containers in the tree, shell in LXC and VMs, start, shut down, stop and restart.
- **Cisco switches:** Ports in the tree with state, VLAN and speed; switch them on and off, change descriptions, save the configuration.
- **WireGuard indicator (optional):** The status bar shows whether the computer is connected to a WireGuard tunnel (tunnel name; connected, stale or disconnected); a click shows details.
- **Git synchronization:** Sync connections, groups, identities, keys and snippets between several devices; passwords and private keys are stored encrypted only (AES-256-GCM).
- **Windows and Linux:** The same experience on both systems, light and dark theme, adjustable font size, interface in German and English.

## Getting started

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e .
sesam                            # or: python -m sesam
```

For a launcher in the application menu / dash see “Installing in the application menu / dash”.

## Screenshots

The screenshots show the German interface; the English interface looks the same apart from the texts.

![Main window (light)](docs/screenshots/hauptfenster-hell.png)

| Dark | Edit connection |
| --- | --- |
| ![Main window (dark)](docs/screenshots/hauptfenster-dunkel.png) | ![“Edit connection” dialog](docs/screenshots/verbindung-bearbeiten.png) |

| File browser (SFTP, light) | File browser (SFTP, dark) |
| --- | --- |
| ![File browser (light)](docs/screenshots/dateibrowser-hell.png) | ![File browser (dark)](docs/screenshots/dateibrowser-dunkel.png) |

| Snippets (light) | Snippets (dark) |
| --- | --- |
| ![Snippets (light)](docs/screenshots/snippets-hell.png) | ![Snippets (dark)](docs/screenshots/snippets-dunkel.png) |

The images show sample data (made-up hosts) and were created with the Qt platform plugin “offscreen”; real windows may differ in frame and font depending on the desktop.

## Appearance

There is no menu bar: all actions (Connect, Files, New connection, New group, Edit, Duplicate, Delete, Sync now, Synchronization, Identities, Keys, Terminals)
are stacked in a sidebar on the right edge (Ctrl+Q quits the program; *Import from ~/.ssh/config* is in the right-click menu of the connection list); the keyboard shortcuts (Enter, Ctrl+N, F2, Del, Ctrl+D, F5 …) work in the whole window.
*Duplicate* creates a copy and opens it for editing right away with the name selected so you can overwrite it; if you cancel, the copy remains.

At the bottom of the sidebar three icons switch between **Light** (sun), **Dark** (moon) and **Same as system** (monitor); the change takes effect immediately and is saved.
Below them the **Settings** button opens the settings dialog (font size, language and WireGuard indicator, see below).
With *Same as system*, Sesam follows the operating system’s light/dark switch where Qt reports it (Windows, macOS, Linux depending on the desktop);
otherwise you set it manually.

**Font size:** in the *Settings* dialog (80 to 200 % of the system font), or by keyboard with **Ctrl + Plus** (larger), **Ctrl + Minus** (smaller) and **Ctrl + 0** (back to 100 %).
Icons and sidebar grow with it. The setting applies immediately, is saved, and stays local (it is not synchronized via Git).

**Language:** Sesam is available in German and English. In the *Settings* dialog choose *System default* (German if the system runs in German, otherwise English), *Deutsch* or *English*.
The language applies after the next start of Sesam and is stored locally (not synchronized via Git). This also applies to the command-line mode (`sesam --connect`).
The source texts are German; `src/sesam/i18n.py` returns the English version from `src/sesam/translations_en.py` via `tr("German text")`. Add new texts there –
the test `tests/test_i18n.py` fails as long as a text is missing from the catalog. Another language needs a second catalog file and an entry in `i18n.LANGUAGES`.

The look (colors, spacing, icons) comes from `src/sesam/gui/style.py`: a shared stylesheet for light and dark, generated from a
color table (`TOKENS`). You change colors in that one place; icons are simple line drawings in the same module.
Sesam uses the Qt style “Fusion” everywhere so that it looks the same on Windows and Linux.

## Installing in the application menu / dash (Linux)

A script sets Sesam up for your user (no root needed): launcher in the application menu or dash, icon, and the `sesam` command.

```bash
./packaging/linux/install.sh --dev        # runs directly from the project folder (.venv), always current code
./packaging/linux/install.sh              # or: from the built program (dist/sesam, see “Building the program”)
./packaging/linux/install.sh --uninstall  # removes everything again; ~/.config/sesam is kept
```

After code changes, `--dev` only needs a restart of Sesam; with the built program you have to rebuild and run the script again.
The `sesam` command lives in `~/.local/bin` (on some distributions the folder must first be added to your `PATH`).

## Groups

Connections can be organized in nested groups (path with “/”, e.g. `Customer A/Production`).
- *New group …* (Ctrl+Shift+N, sidebar or context menu) also creates empty groups; the context menu of a group offers: subgroup, rename/move, delete.
- Use **drag & drop** to move connections and whole groups; dropping on empty space means the top level.
- **Multiple selection:** select several connections with Ctrl or Shift click (or Ctrl+A). In the context menu (right-click in the selection) you can set a common *Color* for all of them or delete them at once; *Del* deletes too. With drag & drop all selected connections move to the target group together. Groups in the selection are left untouched by these actions; Connect, Edit and Duplicate still act on the entry you clicked last.
- Deleting a group does not remove any connections; they move up into the parent group.
- New connections land in the currently selected group. The search field also filters by group.
- **Columns:** Right-click the column headers to show or hide *Target*, *Terminal*, *Tags* and *Operating system* (the name is always visible). The choice is stored locally. *Operating system* is hidden at first.
- **Operating system of the host:** On the first connect Sesam queries the operating system in the background over a second SSH connection (`/etc/os-release`, macOS, `uname`, Windows `ver`) and stores it with the connection, so it is in the column at every start. This query never accepts an unknown host key by itself: it waits until you have confirmed it in the terminal. Connections with a jump host are not queried. *Detect operating system* in the connection's context menu determines the value again, including a password prompt.
- Sesam remembers what is expanded and collapsed: at the next start exactly the groups that were collapsed when you quit are collapsed. This state (like the column widths and the window size, also maximized) stays local and is not synchronized via Git.

## Colors and tags

- **Color:** connections and groups can get one of eight colors; it tints the icon in the tree (the row itself stays unchanged and remains readable in the dark theme).
  For connections you pick it in the edit dialog (*Color*) or by right-click → *Color*, for groups by right-click → *Color*. A group color moves along when you rename or move the group.
- **Tags:** a connection can carry several tags (*Tags* in the edit dialog, separated by commas, e.g. `prod, customer-a`). The dialog suggests tags that already exist.
  The tree shows them in the *Tags* column, and the search field finds them just like names, hosts and groups. Case does not matter.
- Colors and tags are synchronized too (group colors in `groups.json`). If the same group color was changed in conflicting ways, the local one wins.
- The column widths of the main view are reset once to the defaults because a column was added.

## Configuring terminals

Under *Terminals …* in the sidebar you create terminals as command templates and choose a default terminal.
A connection can use a different terminal; the context menu has “Connect with …”.

| Placeholder | Meaning |
|---|---|
| `{ssh_args}` | ssh command as separate arguments |
| `{ssh_command}` | ssh command as a single string |
| `{title}` `{host}` `{user}` `{port}` `{destination}` | connection data |

Examples: `gnome-terminal --title {title} -- {ssh_args}`, `wt.exe new-tab --title {title} {ssh_args}`,
`cmd.exe /k {ssh_command}`, `putty.exe -ssh -P {port} {destination}`.

On first start, terminals found on the system are preconfigured.

You add your own terminals (e.g. Ghostty) via *New ▾ → Custom terminal*. With “Choose program …” you can pick the
executable if it is not on the `PATH` (e.g. an AppImage). Templates for terminals that are not installed
are also under *New ▾*.

## Identities

An identity bundles **user, key file and password** and can be used by any number of connections.
- Manage them under *Identities …* in the sidebar or directly when creating a connection via *Identity → New …*.
- If you choose an identity in a connection, it replaces the connection’s user, key file and password (the fields are then locked; your own values are kept and apply again with “None”).
- The password lives in the keyring, once per identity. If a password changes, it is updated in one place only. This also applies to sudo.
- Deleting an identity also removes it from all connections that use it (they then need their own credentials again).

## SSH keys

*Keys …* in the sidebar manages keys directly in Sesam – the same on Windows and Linux.
- **Generate:** Ed25519 (recommended) or RSA 4096, optionally with a passphrase. The format is standard OpenSSH, understood by `ssh-keygen` and terminals.
- **Import:** read an existing private key file (OpenSSH or classic PEM). If a `.pub` file sits next to it, it is used for protected keys; otherwise Sesam asks for the passphrase once.
- **View:** type, fingerprint (SHA256 as with `ssh-keygen -l`), comment, creation date, public key and who uses the key. *Copy public key* puts the line for `authorized_keys` on the clipboard.
- **Install on server:** like `ssh-copy-id`. You choose a connection and Sesam adds the key to `~/.ssh/authorized_keys` (permissions are set, duplicate entries avoided). Logging in must work once for this; the password is asked for if needed.
- **Assign:** in a connection and in an identity under *Saved key*. It then replaces the key file and applies to terminal, SFTP and Docker. An identity takes precedence over the connection.
- **Delete:** removes the key (private and public) and clears assignments. Public keys installed on servers remain there.
- **Storage location:** the private part is a file (permissions 0600) in the configuration folder under `keys/`, not in the keyring, because `ssh -i` needs a file and the Windows store is too small for large keys.
- **Synchronization:** name, type, fingerprint and public key are always synchronized. The **private part** travels only encrypted and only if password synchronization (with a sync password) is active. Without it, Sesam shows “private key on this device: no” on other devices.

## sudo

Per connection you can enable *With sudo*: after login the session switches directly to root or another user
(`sudo -i` = login shell, `sudo -s` = shell keeping the current environment; target user optional). In addition, the context menu has
*Connect as root (sudo -i)* for a one-off switch without changing the connection.

**sudo without typing the password again:** if a password is saved for the connection and *Also use the saved password for sudo*
is enabled (default), it is used for sudo automatically. It is never passed as a command-line argument but handed into the session
as an environment variable `LC_SSHV_PW` (`ssh -o SendEnv=…`). On the server `sudo -S -v` checks it exactly
once, after which `sudo -i`/`sudo -s` starts without a prompt.
- The server must allow `LC_*` via `AcceptEnv` (Debian/Ubuntu: default). Otherwise, or with a wrong password, sudo asks in the terminal as usual.
- The password is briefly readable in the session environment for your own user and root on the server. The variable is removed for sudo itself.
- Also works with key-based login: the saved password is then only needed for sudo.

## Passwords

A password can be stored per connection. It is saved in the operating system’s keyring
(Secret Service/GNOME Keyring/KWallet on Linux, Credential Manager on Windows), **not** in `config.json`.
When connecting, the terminal starts `ssh` through a small wrapper that supplies the password via `SSH_ASKPASS`
(OpenSSH ≥ 8.4). Notes:
- Password prompts are answered automatically, other prompts (passphrase, 2FA code) are not.
- Unknown hosts are added to `known_hosts` automatically (`StrictHostKeyChecking=accept-new`); changed keys are still rejected. If you set the option yourself under “Additional options”, yours takes precedence.

## Snippets

*Snippets …* (Ctrl+Shift+S, sidebar) stores commands that run in the terminal on one or more hosts.
- **Create:** name, command (also multi-line, e.g. `df -h && free -m`), optionally a **category** (subfolders with “/”, e.g. `Docker/Logs`) and a note. The command applies to all connections.
- **Run:** right-click a connection → *Run snippet* (categories appear as submenus), or tick the targets at the bottom of the snippets window. With multiple selection (Ctrl/Shift click) the snippet runs on all selected connections; one terminal opens per connection, and Sesam asks first. An error on one target (e.g. a missing key) does not stop the others.
- **With sudo:** only the command runs via `sudo -i` (the connection’s target user, otherwise root). The saved password is used as with the connection’s *With sudo*. A shell left open afterwards belongs to the normal user again.
- **Afterwards:** *Keep shell open* (default), *Wait for a key* or *Close terminal immediately*.
- The command runs in `sh -c` on the target system. The connection’s “Remote command” and “With sudo” are replaced for the snippet.
- **Synchronization:** snippets live in the Git repository (`snippets/`) in plain text and reach all devices. Do not write passwords or tokens into commands.

## Transferring files (SFTP)

*Files (SFTP)* (F4, sidebar or the connection’s context menu) opens a file browser: your computer on the left, the server on the right.
- **Transfer:** drag files and folders between the sides with **drag & drop**, use the *Upload* / *Download* buttons, or the context menu.
  On the server side you can also drop files straight from the file manager; if you drop onto a folder, they land inside it.
- **Progress:** each transfer runs in the background with its own progress bar and can be cancelled (a half-transferred file is removed). Several transfers run at the same time.
- **Existing files:** if the target already has entries with the same name, Sesam asks: *Overwrite*, *Skip* or *Cancel*. The choice applies to top-level entries; files inside transferred folders are replaced on *Overwrite*.
- **Manage:** *New folder*, *Rename* (F2) on both sides, *Delete* (Del) on the server (after confirmation, folders including their contents). Path bar (Ctrl+L), “Up one level”, Home folder and Refresh (F5) exist per side; the columns are sortable.
- **Login:** as with the terminal, i.e. with user, key file or the password saved in the keyring (also via an identity). If nothing is stored or it is rejected, Sesam asks once; this password only applies to the open window and is not saved.
  Unknown hosts are added to `known_hosts` as with ssh; changed keys are rejected.
- **Limits:** connections with a **jump host** are not supported yet; “Additional options” and *With sudo* do not apply to file transfer (it works as the connection’s user, not as root).
  Links to folders are not followed on download (protection against loops); links to files are transferred as files.
- The transfer uses the Python library *paramiko*, so it needs no `sftp`/`scp` on the machine.

## Docker containers

If Docker runs on a host, you can connect straight into a container. In the connection (*Edit*) tick
**Show this host’s Docker containers in the connection tree**; optionally set a default shell and user for the containers.
- **In the tree:** the host gets an arrow. On expanding, Sesam asks the host over SSH for its containers (`docker ps -a`) and shows them as child items
  (name, image, status). Stopped containers are greyed out. It only connects on expanding; *Refresh containers* (context menu) reloads.
- **Control:** the context menu of a container offers *Start*, *Stop*, *Restart* and *Delete …* (`docker start`, `stop`, `restart`, `rm`).
  Stop, restart and delete ask first. Delete removes only the container, not its volumes; a container that is still running is stopped in the process (`docker rm -f`).
  Sesam then reloads the list. A shell can only be opened in running containers.
- **Connect:** double-click or Enter opens a shell in the container in the terminal (`docker exec -it`). Without a fixed shell Sesam uses `bash`, otherwise `sh`.
  *Connect as …* asks for shell and user (e.g. `root`) for this one call; *Connect with …* picks a different terminal.
- **Login:** the connection’s password and key are used as for terminal and SFTP. If none is saved, Sesam asks once
  (the password stays in memory only). Connections through a jump host do not show containers yet.
- The Docker user needs access to the Docker socket (group `docker`); `sudo docker` is not used. The settings are synchronized,
  the containers themselves are not.

## Proxmox

If a host is a Proxmox VE server, Sesam shows its guests in the tree. In the connection (*Edit*) tick
**Show this host’s Proxmox guests (VMs and LXC) in the connection tree** (replaces the Docker view); optionally enter the user for VMs (default `root`).
- **In the tree:** on expanding, Sesam asks the host over SSH as `root` with `pvesh` for all VMs and LXC containers (including other cluster nodes). Running guests are coloured, stopped ones grey; templates are hidden.
- **LXC containers:** double-click opens `pct enter <ID>` in the terminal through the host (for guests on other nodes via SSH through that node).
- **VMs:** Sesam asks for the IP address through the QEMU guest agent and connects over SSH from the Proxmox host into the VM. The agent must be running in the VM.
- **Power:** right-click → *Start*, *Shut down*, *Stop (forced)*, *Restart*. Stop and restart ask for confirmation first; afterwards Sesam reloads the status.
- As with Docker, the file browser and jump hosts only work for the host itself. The settings are synchronized, the guests themselves are not.

## Cisco devices

For switches running Cisco IOS, IOS-XE or NX-OS, Sesam shows the ports in the tree. In the connection (*Edit*) tick
**Show this Cisco device’s ports … in the connection tree** (replaces the Docker and Proxmox views). The user needs privilege level 15.
- **In the tree:** on expanding, Sesam opens an SSH shell on the device and reads `show interfaces status`. Each port shows its state (*connected*, *notconnect*, *off*, *err-disabled* …), description, VLAN and speed.
- **Switch on/off:** right-click → *Shut down port* (`shutdown`, with confirmation) or *Enable port* (`no shutdown`).
- **Description:** right-click → *Change description …* sets `description`; empty removes it.
- **Save:** *Save configuration* (on the device or on a port) writes the running configuration permanently (`write memory`, on NX-OS `copy running-config startup-config`). Port changes take effect immediately, but without saving they only last until the device restarts.
- Double-clicking a port opens the device in the terminal. As with Docker and Proxmox, the file browser and jump hosts only work for the device itself.

## WireGuard indicator

If you use WireGuard, switch the indicator on under **Settings › WireGuard**. It is off by default: Sesam then queries nothing and shows nothing.

At the bottom right of the status bar, Sesam shows whether this computer is connected to a WireGuard tunnel, for example `WireGuard · VZS · connected`. The tunnel name is the name of the WireGuard interface (Linux) or of the tunnel (Windows).

| Indicator | Meaning |
|---|---|
| green, *connected* | A tunnel is active. |
| yellow, *stale* | A tunnel is active, but the last handshake is older than three minutes. The tunnel may be stuck. |
| gray, *disconnected* | No tunnel is active. |
| dashed, *unavailable* | The state cannot be determined on this system. |

Clicking the indicator shows details: endpoint, tunnel address, allowed IPs, last handshake and data volume. Sesam reads the data volume without administrator rights from the counters of the interface (Linux) or network adapter (Windows). WireGuard (`wg show`) provides endpoint and handshake only with administrator rights and only if the `wg` program is installed (package `wireguard-tools`). Without them, Sesam only detects *that* a tunnel is running and never shows “stale”.

**sudo password (Linux, optional):** When you switch the indicator on, Sesam asks for your sudo password. With a valid password Sesam reads endpoint and handshake via `sudo wg show` (at most every 30 seconds; the private key is never requested). The password is passed to sudo only through standard input and stored in the system keyring; switching the indicator off deletes it. If the field stays empty, you cancel or the password is wrong, Sesam shows the simple display; a rejected password is not tried again, so that sudo does not lock the account. With several tunnels, the first name is shown with a suffix, such as `VZS +1`.

The state is checked in the background every 5 seconds (Windows: every 15 seconds). Linux: Sesam reads `/sys/class/net`. Windows: Sesam asks PowerShell for the “WireGuard Tunnel” network adapters. Sesam neither sets up nor disconnects tunnels itself.

## Synchronization via Git

*Synchronization …* in the sidebar syncs connections, groups, identities, keys and snippets through a Git repository (terminals and
window widths stay per device). The tool uses the installed `git`. Login runs in the background without prompts:
- **SSH key** (`git@server:…`): as usual with `git`.
- **Username and password or token** (`https://…`): enter them in the dialog under *Username* and *Password / token*. The password lives in the system keyring
  (not in the configuration file) and is passed to git only through the process environment, never on the command line or in the URL.
  For GitHub, GitLab and Gitea an access token is usually needed instead of the account password. If you delete the username, Sesam also removes the saved password.
- **Git credentials already saved:** if the username stays empty, the `git` settings apply (e.g. `git credential` helpers; log in once beforehand via *Test connection* or `git ls-remote`).

- **Automatic:** at startup and shortly after every change. *F5* or *Sync now* triggers the sync manually; the status is shown at the bottom right.
- **Repository layout:** one JSON file per entry (`connections/`, `identities/`, `ssh_keys/`, `snippets/`), `groups.json`, `meta.json`. The repository may be private, but plain-text data such as host names and users is visible in it.
- **Passwords** are stored only encrypted (scrypt + AES-256-GCM) and written to the keyring on every device. You set the **sync password** on the first device and enter it on every further one. It cannot be recovered and cannot easily be changed. Without password sync, existing encrypted entries in the repository are kept.
- **Merging:** a three-way comparison per entry against the state of the last sync. Changes to different entries are combined, deletions are applied. If the same entry was changed differently on two devices, your local version stays and the other appears as “… (conflict)”.
- If an identity or key is deleted, connections that use it lose the reference.
- Creating an empty repository (e.g. on GitHub/GitLab/Gitea) is enough; the tool creates the branch itself.

## Data

Configuration: `~/.config/sesam/config.json` (Linux), `%APPDATA%\sesam\config.json` (Windows).
Passwords are never in this file but in the operating system’s keyring; without a saved password, login goes through ssh (key, agent, prompt in the terminal).

If `config.json` is damaged (not valid JSON or similar), Sesam does not delete it: the file is backed up next to it as `config.json.defekt-<date>-<time>`,
Sesam shows a message with the reason and starts with an empty configuration. You can repair the backup and copy it back as `config.json`.
With Git synchronization active, set it up again; the connections then come back from the repository. If the file can neither be read nor backed up,
Sesam does not start and changes nothing.

Old data from the earlier version (“ssh_verwaltung”) is taken over automatically at first start: the configuration folder is renamed, passwords move within the keyring.

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
