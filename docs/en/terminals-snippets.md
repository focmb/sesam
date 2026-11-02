# Terminals and snippets

← [Back to the README](../../README.en.md) · *[Deutsche Fassung](../terminals-snippets.md)*

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

## Integrated terminal

The *Integrated (in Sesam)* entry opens the connection as a tab in the main window, without an external terminal program.
It is available alongside the other terminals (as the default or per connection, also via “Connect with …”) and cannot be deleted.

- **Tabs:** The connection list stays in front, each session gets its own tab. The tab closes as soon as `ssh` ends; closing a running session asks first.
- **Usage:** Selecting with the mouse copies to the selection clipboard (middle click pastes), Ctrl+Shift+C / Ctrl+Shift+V or Ctrl+Ins / Shift+Ins copy and paste, Shift+Page Up/Down and the mouse wheel scroll through the history (10,000 lines), Ctrl+wheel changes the tab's font size. A right click shows a menu. Programs such as vim, less or htop run on the alternate screen.
- **Same machinery as external:** The same wrapper runs `ssh`, so `~/.ssh/config`, jump hosts, saved passwords, sudo and the prompt for changed host keys work unchanged.
- **Requirements:** The Python package `pyte` (on Windows also `pywinpty` for ConPTY). The ready-made packages include both; on Arch `python-pyte` is an optional dependency.
- **Limits:** Mouse support inside programs (mouse modes) and Sixel graphics are not interpreted.

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
