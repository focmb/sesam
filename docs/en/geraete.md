# Docker, Proxmox, Cisco and WireGuard

← [Back to the README](../../README.en.md) · *[Deutsche Fassung](../geraete.md)*

## Docker containers

If Docker runs on a host, you can connect straight into a container. In the connection (*Edit*, tab *Tree extensions*) tick
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

If a host is a Proxmox VE server, Sesam shows its guests in the tree. In the connection (*Edit*, tab *Tree extensions*) tick
**Show this host’s Proxmox guests (VMs and LXC) in the connection tree** (replaces the Docker view); optionally enter the user for VMs (default `root`).
- **In the tree:** on expanding, Sesam asks the host over SSH as `root` with `pvesh` for the VMs and LXC containers of **this node**. In a cluster you add each node as its own connection; each shows only its own guests, not those of the whole cluster. Running guests are coloured, stopped ones grey; templates are hidden.
- **LXC containers:** double-click opens `pct enter <ID>` in the terminal through the host (for guests on other nodes via SSH through that node).
- **VMs:** Sesam asks for the IP address through the QEMU guest agent and connects over SSH from the Proxmox host into the VM. The agent must be running in the VM.
- **Power:** right-click → *Start*, *Shut down*, *Stop (forced)*, *Restart*. Stop and restart ask for confirmation first; afterwards Sesam reloads the status.
- As with Docker, the file browser and jump hosts only work for the host itself. The settings are synchronized, the guests themselves are not.

## Cisco devices

For switches running Cisco IOS, IOS-XE or NX-OS, Sesam shows the ports in the tree. In the connection (*Edit*, tab *Tree extensions*) tick
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
