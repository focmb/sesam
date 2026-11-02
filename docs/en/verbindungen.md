# Managing connections

← [Back to the README](../../README.en.md) · *[Deutsche Fassung](../verbindungen.md)*

## Appearance

The *Edit connection* dialog is split into four tabs: *General* (name, host, group, color, tags, terminal, notes), *Access* (identity, user, port, key, password), *Advanced* (jump host, extra options, remote command, sudo) and *Tree extensions* (Docker, Proxmox, Cisco). This keeps it usable on small screens.

There is no menu bar: all actions (Connect, Files, New connection, New group, Edit, Duplicate, Delete, Sync now, Synchronization, Identities, Keys, Terminals)
are stacked in a sidebar on the right edge (Ctrl+Q quits the program; *Import from ~/.ssh/config* is in the right-click menu of the connection list); the keyboard shortcuts (Enter, Ctrl+N, F2, Del, Ctrl+D, F5 …) work in the whole window.
*Duplicate* creates a copy and opens it for editing right away with the name selected so you can overwrite it; if you cancel, the copy remains.

At the bottom of the sidebar three icons switch between **Light** (sun), **Dark** (moon) and **Same as system** (monitor); the change takes effect immediately and is saved.
Below them the **Settings** button opens the settings dialog (font size, language and WireGuard indicator, see [WireGuard indicator](geraete.md#wireguard-indicator)).
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

## Groups

Connections can be organized in nested groups (path with “/”, e.g. `Customer A/Production`).
- *New group …* (Ctrl+Shift+N, sidebar or context menu) also creates empty groups; the context menu of a group offers: subgroup, rename/move, delete.
- Use **drag & drop** to move connections and whole groups; dropping on empty space means the top level.
- **Multiple selection:** select several connections with Ctrl or Shift click (or Ctrl+A). In the context menu (right-click in the selection) you can set a common *Color* for all of them or delete them at once; *Del* deletes too. With drag & drop all selected connections move to the target group together. Groups in the selection are left untouched by these actions; Connect, Edit and Duplicate still act on the entry you clicked last.
- Deleting a group does not remove any connections; they move up into the parent group.
- New connections land in the currently selected group. The search field also filters by group.
- **Columns:** Right-click the column headers to show or hide *Type*, *Target*, *Terminal*, *Tags* and *Operating system* (the name is always visible). The choice is stored locally. *Operating system* is hidden at first.
- **Kind of connection (Type):** The *Type* column behind the name shows small badges: **SSH**, **SSH · VNC** or **SSH · RDP** (SSH with [remote access](fernzugriff.md) through the tunnel), and just **VNC** or **RDP** for devices reached directly. Such direct connections carry a screen instead of the server icon, and the *Target* column shows `vnc://host:port` or `rdp://host:port`. A tooltip on the badge explains the kind. In the search field, `typ:vnc`, `typ:rdp` or `typ:ssh` shows only those connections; combine it with more words (`typ:vnc kiosk`).
- **Operating system of the host:** On the first connect Sesam queries the operating system in the background over a second SSH connection (`/etc/os-release`, macOS, `uname`, Windows `ver`, `show version | include ^Cisco IOS` on Cisco switches) and stores it with the connection, so it is in the column at every start. If the last query is older than 7 days, Sesam refreshes the value on the next connect (if that fails, the old value stays). This query never accepts an unknown host key by itself: it waits until you have confirmed it in the terminal. Connections with a jump host are not queried. *Detect operating system* in the connection's context menu determines the value again, including a password prompt.
- Sesam remembers what is expanded and collapsed: at the next start exactly the groups that were collapsed when you quit are collapsed. This state (like the column widths and the window size, also maximized) stays local and is not synchronized via Git.

## Favorites and quick launcher

- **Favorites:** *Favorite* (Ctrl+B, sidebar or context menu) marks connections. They also appear in a *Favorites* section at the top of the tree (collapsible) and carry a ★ in their group. The flag belongs to the connection, so it is **synced via Git**. Duplicates are not favorites. Under **Settings › Show favorites** favorites can be switched off: the section, the ★ and the favorites entries in the menus then disappear from the main window, while the marks are kept. The setting applies to this computer only.
- **Quick launcher (Ctrl+K):** A search window over all connections. The search is fuzzy (`wbpd` finds `web-prod`), several words must all match, and name, host, user, group and tags are searched. Without a query it shows *Favorites*, *Recently used* (the latest eight, with “5 min ago” etc.) and *All connections*.
  - ↵ connects, Ctrl+↵ opens the files (SFTP), Ctrl+B toggles the favorite, Esc closes.
  - **Recently used** exists only here, not in the tree. The times stay local (not synced) and count on connect, SFTP, Docker/Proxmox/Cisco and when starting a tunnel.

## Colors and tags

- **Color:** connections and groups can get one of eight colors; it tints the icon in the tree (the row itself stays unchanged and remains readable in the dark theme).
  For connections you pick it in the edit dialog (*Color*) or by right-click → *Color*, for groups by right-click → *Color*. A group color moves along when you rename or move the group.
- **Tags:** a connection can carry several tags (*Tags* in the edit dialog, separated by commas, e.g. `prod, customer-a`). The dialog suggests tags that already exist.
  The tree shows them in the *Tags* column, and the search field finds them just like names, hosts and groups. Case does not matter.
- Colors and tags are synchronized too (group colors in `groups.json`). If the same group color was changed in conflicting ways, the local one wins.
- The column widths of the main view are reset once to the defaults because a column was added.

## Open connections

A connection that Sesam has opened in a terminal shows a **green dot** on its server icon – also when it was started from a snippet.
The dot disappears as soon as the terminal is closed.

- **Double-click or Enter** on a connection that is already open does not open a second terminal; it only shows a notice.
- **Right-click → *New session*** deliberately opens another terminal. *Connect as root* and *Connect with …* also always open a new one.
- Sesam detects open connections by the running terminal process. Terminals that detach from their launcher right away (e.g. GNOME Terminal) are not detected; they get no dot and a double-click opens a new terminal as before.
- Sessions in Docker containers, Proxmox guests and on Cisco ports do not count for the host.
