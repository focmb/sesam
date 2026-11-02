# VNC

← [Back to the README](../../README.en.md) · *[Deutsche Fassung](../vnc.md)*

Sesam can also open a device's screen: it starts the **VNC viewer of your choice** (for example Remmina, TigerVNC or KRDC) and, if you want, routes it through an SSH
tunnel. Sesam does not include a viewer of its own.

## Turning on VNC for a connection

*Edit*, tab **VNC**:

| Field | Meaning |
|---|---|
| **VNC** | *Off*, *Through an SSH tunnel* or *Direct* (see below) |
| **Target from the server** | Tunnel only: the machine the server can reach. Empty = the server itself (`localhost`). This also reaches devices on the network behind the server. |
| **VNC port** | Default 5900 (display `:0`), 5901 for `:1` and so on |
| **Viewer** | *Default viewer* or a specific viewer from the list |
| **VNC password** | Optional; stored in the keyring (see below) |

Each connection has exactly one VNC target.

### Through an SSH tunnel

For servers whose VNC port only listens locally or is not exposed. Sesam starts a short-lived `ssh -N -L 127.0.0.1:<free port>:<target>:<VNC port>` with the same
credentials as the connection (identity, key, password, jump host; unknown host keys are trusted only after a prompt, as with
[port forwarding](dateien-tunnel.md)). Once the tunnel is up, the viewer opens on `127.0.0.1:<free port>`. The tunnel does not show up in the list of forwardings.

- **End:** When you close the viewer, the tunnel ends too. If the viewer hands the session over to an already running instance and exits (Remmina does this),
  the tunnel stays up until you choose **Close VNC** in the context menu or quit Sesam.
- **Errors:** If the tunnel fails (for example access denied), Sesam shows the message and ends the session.

### Direct

For devices that only speak VNC (cameras, industrial controllers, thin clients): the viewer connects straight to the connection's **host** and **VNC port**. No SSH is needed.

- **Double-click and Enter** open VNC instead of a terminal for such connections. SSH stays available in the context menu: **Connect via SSH**.
- VNC is **not encrypted** by default. Use this mode only on a trusted network or over a VPN.
- If you quit Sesam, the viewer stays open, like a terminal that is already open.

## Opening and closing

Context menu of the connection: **Open VNC** and **Close VNC**. A connection with a running VNC session carries the green dot, as with terminals.
Choosing **Open VNC** a second time does not start a second viewer.

## Managing viewers

Sidebar **VNC viewers …**. As with terminals, a viewer is a **command template**; Sesam starts the program directly (not through a shell). **New ▾** offers templates for the
usual viewers of the platform; fresh installations already contain the viewers that were found.

| Placeholder | Content |
|---|---|
| `{host}` `{port}` | Target of the viewer (for a tunnel `127.0.0.1` and the local tunnel port) |
| `{display}` | Display number (`port - 5900`) |
| `{url}` | `vnc://host:port` |
| `{title}` | Name of the connection |
| `{profile}` | Remmina profile file created by Sesam |
| `{password_file}` | Password file in `vncpasswd` format (for example for TigerVNC `-passwd`) |

Examples:

```text
remmina -c {profile}
vncviewer -passwd {password_file} {host}::{port}
krdc {url}
```

If a connection has no stored VNC password, `{password_file}` is dropped together with the option before it (`-passwd`, `-PasswordFile=…`).

## VNC password

The password is stored in the operating system's keyring, separate from the connection's SSH password, and **not synchronised**: you enter it once on each device.
VNC only uses the **first 8 characters**.

It is never passed to the viewer as an argument (visible in the process list) but through a file in a temporary folder that only you can read:

- **Remmina:** Sesam writes a profile (`{profile}`) for the session and stores the password encrypted the way Remmina itself does; the key for that is in Remmina's
  `remmina.pref`. The profile is deleted when the session ends.
- **Other viewers:** `{password_file}` points to a file in `vncpasswd` format; it is deleted 30 seconds after the viewer starts.

> **Note:** The hand-over to Remmina follows its profile format and has not been tested with every Remmina version. If it does not work for you, Remmina still opens
> and asks for the password; Sesam shows a notice.

## Synchronisation

The connection's VNC settings (mode, target, port, viewer) are synchronised. The VNC password and the list of viewers are **not**, because viewers and their paths differ
per computer. If the chosen viewer does not exist on a device, Sesam uses the default viewer there.
