# Terminals and snippets

← [Back to the README](../../README.en.md) · *[Deutsche Fassung](../terminals-snippets.md)*

## Configuring terminals

Under *Settings › Terminals …* you create terminals as command templates and choose a default terminal.
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

## Integrated terminal

The *Integrated (in Sesam)* entry opens the connection as a tab in the main window, without an external terminal program.
It is available alongside the other terminals (as the default or per connection, also via “Connect with …”) and cannot be deleted.

- **Tabs:** The connection list stays in front, each session gets its own tab. The tab closes as soon as `ssh` ends; closing a running session asks first.
- **Tiles:** A tab can show several sessions side by side or one below the other. **Ctrl+Shift+O** splits to the right, **Ctrl+Shift+E** downwards (also right click in the terminal or on the tab); the new tile starts the same connection again. In a connection's context menu, **“Open next to/below the current terminal”** opens it as a tile in the most recently used terminal tab. **Alt+arrow keys** move the focus between tiles (the active one has a coloured border), **Ctrl+Shift+Enter** shows the active tile alone in the tab and back, **Ctrl+Shift+W** closes it. The dividers can be dragged with the mouse. The **grip** (six dots) at the top right of a tile lets you **move it by drag and drop**: dropped on the left, right, top or bottom edge of the target tile it is inserted there (the target half is highlighted), dropped in the middle the two tiles swap places. Dropping onto a tile in another tab (hovering over the tab switches to it) moves the tile into that tab; if the old tab becomes empty it disappears. The sessions keep running. When a session ends only its tile disappears; the tab closes with the last one. The tab title shows the active tile, with “(+n)” when there are several. Note: Alt+arrow is not passed on to the program in the terminal.
- **Tile all sessions:** **Ctrl+Shift+G** (or the **Tiles** button next to the tabs, the tab menu and the terminal menu) pulls all open sessions into the current tab and arranges them as a grid (2 → side by side, 3–4 → 2 × 2, 5–6 → 3 × 2 …). **“Split into separate tabs”** turns that back into one tab per session.
- **Send input to all tiles:** **Ctrl+Shift+B** (or the menu check mark) makes everything you type or paste in a tab arrive in **all** tiles of that tab – handy for running the same commands on several servers. Because this is risky it is always **off**, applies to this tab only, switches itself off with fewer than two tiles and is clearly marked: a red border around all tiles, an “Input to all” label in each tile and a ⚠ in the tab title. Multi-line or very long pasted text is confirmed first, and an entry chosen from the clipboard history only goes into the active tile. Ended sessions receive nothing.
- **Usage:** Selecting with the mouse copies to the selection clipboard (middle click pastes), Ctrl+Shift+C / Ctrl+Shift+V or Ctrl+Ins / Shift+Ins copy and paste, Shift+Page Up/Down and the mouse wheel scroll through the history (10,000 lines), Ctrl+wheel changes the tab's font size. A right click shows a menu. Programs such as vim, less or htop run on the alternate screen.
- **Same machinery as external:** The same wrapper runs `ssh`, so `~/.ssh/config`, jump hosts, saved passwords, sudo and the prompt for changed host keys work unchanged.
- **Requirements:** The Python package `pyte` (on Windows also `pywinpty` for ConPTY). The ready-made packages include both; the Arch package pulls in `python-pyte` automatically.
- **Limits:** Mouse support inside programs (mouse modes) and Sixel graphics are not interpreted.

## Clipboard history

Text you copy in Sesam stays available and can be pasted into several terminal tabs.

- **What is recorded:** Copies in the integrated terminal (Ctrl+Shift+C, right click → Copy) and text from other programs as soon as Sesam is in the foreground. While Sesam is in the background it does not read the clipboard. Password managers mark secret copies; Sesam skips those. Empty text and entries over 1 MB are not recorded; the history holds at most 200 entries or 20 MB (older, unpinned ones drop out first).
- **Popup in the terminal:** Ctrl+Shift+H (or right click → History …) opens a list with a search field. Enter pastes the entry, Shift+Enter pastes and runs it, Alt+1…9 picks one of the first nine entries directly, Ctrl+Del deletes. Pinned entries come first and so keep their numbers.
- **“Clipboard …” dialog** (sidebar): preview of long texts too, pin, delete, create your own entries, copy back to the clipboard and **paste into several tabs at once** (tick the tabs, optionally append Enter). Sesam asks first for very long or multi-line text going into several tabs, because a terminal may run it immediately.
- **Tab menu:** right click a tab → “Paste clipboard into all tabs”.
- **Clear all:** in the dialog under “Clear all …”. “Clear history” keeps pinned entries, “Clear all” deletes those too and renews the file's key; optionally the system clipboard is cleared as well.
- **Storage:** The history survives a restart. It lives in `clipboard.json` next to the configuration (readable by the user only), is **encrypted** (AES-GCM, the key is in the keyring) and **never goes through synchronization**. Without a keyring it stays in memory unless you allow unencrypted saving in the dialog. Options: switch the history off entirely, do not save it, clear it on exit (pinned entries stay).

## Connection errors and changed host keys

Every connection starts in the terminal through a small wrapper (even without a stored password) that runs `ssh` as a subprocess.
- **The connection fails** (server switched off, timeout, login refused …): the terminal stays open with the message from `ssh` until you press Enter. Sessions that end normally close as usual.
- **The server’s key has changed** (e.g. a new server with the same IP): after the message the wrapper asks whether to remove the old entry from `known_hosts` with `ssh-keygen -R` and reconnect. On “y” the new key is accepted, on “N” (default) nothing is changed. Only remove the entry if the change was expected – otherwise it could be an attack.
- Terminal templates without `{ssh_args}`/`{ssh_command}` (e.g. PuTTY) start the connection themselves and do not go through the wrapper.

## Snippets

*Snippets …* (Ctrl+Shift+S, sidebar) stores commands that run in the terminal on one or more hosts.
- **Create:** name, command (also multi-line, e.g. `df -h && free -m`), optionally a **category** (subfolders with “/”, e.g. `Docker/Logs`) and a note. The command applies to all connections.
- **Run:** right-click a connection → *Run snippet* (categories appear as submenus), or tick the targets at the bottom of the snippets window. With multiple selection (Ctrl/Shift click) the snippet runs on all selected connections; one terminal opens per connection, and Sesam asks first. An error on one target (e.g. a missing key) does not stop the others.
- **With sudo:** only the command runs via `sudo -i` (the connection’s target user, otherwise root). The saved password is used as with the connection’s *With sudo*. A shell left open afterwards belongs to the normal user again.
- **Afterwards:** *Keep shell open* (default), *Wait for a key* or *Close terminal immediately*.
- The command runs in `sh -c` on the target system. The connection’s “Remote command” and “With sudo” are replaced for the snippet.
- **Synchronization:** snippets live in the Git repository (`snippets/`) in plain text and reach all devices. Do not write passwords or tokens into commands.
