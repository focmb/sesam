# Credentials and sudo

← [Back to the README](../../README.en.md) · *[Deutsche Fassung](../zugangsdaten.md)*

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
- With a stored password, Sesam asks in the terminal for unknown hosts (target and jump hosts), shows the fingerprint and adds them to `known_hosts` only after “y” (needs `ssh-keygen` and `ssh-keyscan`); changed keys are still rejected. Automatic password entry only answers prompts of the target host, not those of a jump host. If you set the option yourself under “Additional options”, yours takes precedence.
