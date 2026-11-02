# Files and tunnels

← [Back to the README](../../README.en.md) · *[Deutsche Fassung](../dateien-tunnel.md)*

## Transferring files (SFTP)

*Files (SFTP)* (F4, sidebar or the connection’s context menu) opens a file browser: your computer on the left, the server on the right.
- **Transfer:** drag files and folders between the sides with **drag & drop**, use the *Upload* / *Download* buttons, or the context menu.
  On the server side you can also drop files straight from the file manager; if you drop onto a folder, they land inside it.
- **Progress:** each transfer runs in the background with its own progress bar and can be cancelled (a half-transferred file is removed). Several transfers run at the same time.
- **Existing files:** if the target already has entries with the same name, Sesam asks: *Overwrite*, *Skip* or *Cancel*. The choice applies to top-level entries; files inside transferred folders are replaced on *Overwrite*.
- **Manage:** *New folder*, *Rename* (F2) on both sides, *Delete* (Del) on the server (after confirmation, folders including their contents). Path bar (Ctrl+L), “Up one level”, Home folder and Refresh (F5) exist per side; the columns are sortable.
- **Login:** as with the terminal, i.e. with user, key file or the password saved in the keyring (also via an identity). If nothing is stored or it is rejected, Sesam asks once; this password only applies to the open window and is not saved.
  Unknown hosts are added to `known_hosts` only after you confirm the fingerprint (SHA256); changed keys are rejected.
- **Limits:** connections with a **jump host** are not supported yet; “Additional options” and *With sudo* do not apply to file transfer (it works as the connection’s user, not as root).
  Links to folders are not followed on download (protection against loops); links to files are transferred as files.
- The transfer uses the Python library *paramiko*, so it needs no `sftp`/`scp` on the machine.

## Port forwarding (tunnels)

A port forwarding lets you reach services that only run on the server or in its network through the SSH connection. They are saved per connection
(*Edit*, tab **Port forwarding**) and started and stopped in the connection's context menu under **Port forwarding**
(a check mark shows that the tunnel is running; there are also *Start all* and *Stop all*).

- **Local (`-L`):** A port on this computer leads through the server to a destination the server can reach. Example: local port `5432` →
  destination `localhost:5432` opens a database that only listens on the server; local port `8443` → destination `192.168.3.20:443` opens the web interface
  of a device in the network behind the server (`https://localhost:8443` in the browser).
- **Remote (`-R`):** A port on the server leads to a destination on this computer, e.g. to make a local development server reachable from the server.
- **Dynamic (`-D`):** A SOCKS proxy on this computer. Programs that use it can reach everything the server can reach.
- **Listen address:** Empty means this computer only (`127.0.0.1`; for remote forwardings `localhost` on the server). An open address such as `0.0.0.0` makes the
  tunnel reachable for every computer on the network, without any login; Sesam therefore asks when you save.
- **Display:** While a tunnel runs, the connection name is followed by ⇄ and the number of tunnels. Sesam reports when a tunnel is active or ends on its own
  (port in use, login refused, connection lost), including the message from `ssh`.
- **Access:** The connection's user, key, identity, jump host and “Additional options” also apply to the tunnel; a saved password is passed on like for the terminal. A tunnel has no
  terminal and cannot ask anything: without a saved password it only works with a key that has no passphrase (or a loaded `ssh-agent`). The connection's remote
  command and sudo are ignored.
- **Unknown hosts:** If the host is not in `known_hosts` yet, Sesam shows the fingerprints before starting and asks whether to trust it.
- **Ending:** Every forwarding runs as its own `ssh -N` process; tunnels end when Sesam quits (or when the forwarding is deleted or changed).
- **Synchronization:** The forwardings belong to the connection and are synchronized (and signed) with it; nothing is ever started automatically.
