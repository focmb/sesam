# Remote access (VNC and RDP)

← [Back to the README](../../README.en.md) · *[Deutsche Fassung](../fernzugriff.md)*

Sesam can also open a device's screen: it starts the **VNC or RDP viewer of your choice** (for example Remmina, TigerVNC, FreeRDP or `mstsc`) and, if you want, routes it
through an SSH tunnel. Sesam does not include a viewer of its own.

## Turning on remote access for a connection

*Edit*, tab **Remote access**:

| Field | Meaning |
|---|---|
| **Remote access** | *Off*, *VNC* or *RDP* |
| **Type** | *Through an SSH tunnel* or *Direct* (see below) |
| **Target from the server** | Tunnel only: the machine the server can reach. Empty = the server itself (`localhost`). This also reaches devices on the network behind the server. |
| **Port** | Default 5900 for VNC (display `:0`, 5901 for `:1` and so on), 3389 for RDP |
| **Viewer** | *Default viewer* or a specific viewer of the protocol |
| **User, domain, extra arguments** | RDP only; Sesam appends the extra arguments (for example `/f` for full screen) to the viewer command |
| **Password** | Optional; stored in the keyring (see below) |

Each connection has exactly one target: either VNC or RDP. If you change the protocol, the stored password of the old protocol is deleted.

### Through an SSH tunnel

For servers whose VNC or RDP port only listens locally or is not exposed. Sesam starts a short-lived `ssh -N -L 127.0.0.1:<free port>:<target>:<port>` with the same
credentials as the connection (identity, key, password, jump host; unknown host keys are trusted only after a prompt, as with [port forwarding](dateien-tunnel.md)).
Once the tunnel is up, the viewer opens on `127.0.0.1:<free port>`. The tunnel does not show up in the list of forwardings.

- **End:** When you close the viewer, the tunnel ends too. If the viewer hands the session over to an already running instance and exits (Remmina does this),
  the tunnel stays up until you choose **Close VNC** or **Close RDP** in the context menu or quit Sesam.
- **Errors:** If the tunnel fails (for example access denied), Sesam shows the message and ends the session.
- **RDP certificate:** Because the viewer connects to `127.0.0.1`, the name in the target's certificate does not match. Depending on the viewer, a prompt appears; the
  FreeRDP templates remember the certificate on first use (`/cert:tofu`).

### Direct

For devices that only speak VNC or RDP (cameras, industrial controllers, thin clients, Windows machines without SSH): the viewer connects straight to the connection's
**host** and **port**. No SSH is needed.

- **Double-click and Enter** open remote access instead of a terminal for such connections. SSH stays available in the context menu: **Connect via SSH**.
- VNC is **not encrypted** by default. Use this mode only on a trusted network or over a VPN.
- If you quit Sesam, the viewer stays open, like a terminal that is already open.

## Opening and closing

Context menu of the connection: **Open VNC** or **Open RDP**, and **Close …**. A connection with a running session carries the green dot, as with terminals.
Opening it a second time does not start a second viewer.

## Managing viewers

Sidebar **Viewers …**. At the top you choose the protocol (VNC or RDP); each has its own list and its own default viewer. As with terminals, a viewer is a
**command template**; Sesam starts the program directly (not through a shell). **New ▾** offers templates for the usual viewers of the platform; fresh installations and
configurations from older versions already contain the viewers that were found.

| Placeholder | Content |
|---|---|
| `{host}` `{port}` | Target of the viewer (for a tunnel `127.0.0.1` and the local tunnel port) |
| `{display}` | VNC: display number (`port - 5900`) |
| `{url}` | `vnc://host:port` or `rdp://host:port` |
| `{title}` | Name of the connection |
| `{user}` `{domain}` | RDP: user and domain; when empty, the whole argument (`/u:{user}`) is dropped |
| `{profile}` | Remmina profile file created by Sesam |
| `{password_file}` | VNC: password file in `vncpasswd` format (for example for TigerVNC `-passwd`) |
| `{rdp_file}` | RDP: `.rdp` file created by Sesam (for `mstsc`) |
| `/from-stdin` | RDP (FreeRDP): Sesam passes the password through standard input |

Examples:

```text
remmina -c {profile}
vncviewer -passwd {password_file} {host}::{port}
xfreerdp /v:{host}:{port} /u:{user} /d:{domain} /cert:tofu +clipboard /dynamic-resolution /from-stdin
mstsc {rdp_file}
```

If a connection has no stored password, `{password_file}` is dropped together with the option before it (`-passwd`, `-PasswordFile=…`). The FreeRDP templates exist for
`xfreerdp` (FreeRDP 2) and `xfreerdp3` (FreeRDP 3); FreeRDP 3 knows the option `/cert:tofu`, in FreeRDP 2 it is called `/cert-tofu`.

## Password

The password is stored in the operating system's keyring, separate from the connection's SSH password, and **not synchronised**: you enter it once on each device.
VNC only uses the **first 8 characters**.

It is never passed to the viewer as an argument (visible in the process list). Instead:

| Viewer | Hand-over |
|---|---|
| Remmina (VNC and RDP) | Sesam writes a profile (`{profile}`) for the session into a temporary folder that only you can read and stores the password encrypted the way Remmina itself does; the key for that is in Remmina's `remmina.pref`. The profile is deleted when the session ends. |
| Viewers with `{password_file}` (VNC) | File in `vncpasswd` format in the temporary folder; it is deleted 30 seconds after the viewer starts. |
| FreeRDP (`/from-stdin`) | Through the viewer's standard input. |
| `mstsc` and others | The password is **not** passed, the viewer asks for it (Sesam shows a notice). The `.rdp` file contains the address and user, never the password. |

> **Note:** The hand-over to Remmina and FreeRDP follows their profile format and parameters and has not been tested with every version. If it does not work for you,
> the viewer still opens and asks for the password.

## Synchronisation

The connection's settings (protocol, type, target, port, viewer, user, domain, extra arguments) are synchronised. The password and the list of viewers are **not**,
because viewers and their paths differ per computer. If the chosen viewer does not exist on a device, Sesam uses the protocol's default viewer there.

## From version 3.0

Configurations with VNC from version 3.0 are taken over automatically on start (protocol VNC, same type, same port); the stored VNC password is kept.
