"""SSH-Schlüssel: erzeugen, aus Dateien einlesen, Fingerabdruck, öffentlichen Schlüssel auf einem Server eintragen."""
from __future__ import annotations

import base64
import hashlib
import shlex
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from . import sftp
from .models import Config, Connection, SshKey

TYPES = {"ed25519": "Ed25519 (empfohlen)", "rsa": "RSA (4096 Bit)"}
RSA_BITS = 4096


class KeyManagementError(Exception):
    """Fehler mit einer für Anwender lesbaren Meldung."""


@dataclass(frozen=True)
class KeyMaterial:
    private: str  # privater Schlüssel im OpenSSH-Format (ggf. passphrasengeschützt)
    public: str  # „ssh-ed25519 AAAA…“ ohne Kommentar
    type: str
    fingerprint: str
    encrypted: bool


def _crypto():
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ed25519, rsa
    except ImportError as exc:
        raise KeyManagementError("Das Paket „cryptography“ ist nicht installiert.") from exc
    return serialization, ed25519, rsa


def fingerprint(public: str) -> str:
    """SHA256-Fingerabdruck wie ihn ``ssh-keygen -l`` anzeigt."""
    try:
        blob = base64.b64decode(public.split()[1], validate=True)
    except (IndexError, ValueError) as exc:
        raise KeyManagementError("Kein gültiger öffentlicher Schlüssel.") from exc
    return "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")


def type_of(public: str) -> str:
    name = public.split()[0] if public.split() else ""
    if name == "ssh-ed25519":
        return "ed25519"
    if name == "ssh-rsa":
        return "rsa"
    if name.startswith("ecdsa-"):
        return "ecdsa"
    if name.startswith("sk-"):
        return "sk"
    return name or "?"


def generate(kind: str = "ed25519", passphrase: str = "") -> KeyMaterial:
    serialization, ed25519, rsa = _crypto()
    if kind == "ed25519":
        key = ed25519.Ed25519PrivateKey.generate()
    elif kind == "rsa":
        key = rsa.generate_private_key(public_exponent=65537, key_size=RSA_BITS)
    else:
        raise KeyManagementError(f"Unbekannter Schlüsseltyp: {kind}")
    algo = (serialization.BestAvailableEncryption(passphrase.encode("utf-8")) if passphrase
            else serialization.NoEncryption())
    private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH, algo).decode("ascii")
    public = key.public_key().public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH).decode("ascii")
    return KeyMaterial(private, public, kind, fingerprint(public), bool(passphrase))


def read_key(text: str, passphrase: str | None = None, public_hint: str | None = None) -> KeyMaterial:
    """Liest einen privaten Schlüssel (OpenSSH- oder PEM-Format).

    Ist er durch eine Passphrase geschützt, braucht es sie oder den öffentlichen Schlüssel (``public_hint``, z. B. aus der .pub-Datei),
    um den öffentlichen Teil zu bestimmen. Bei fehlender Angabe: ``NeedsPassphrase``.
    """
    serialization, _ed, _rsa = _crypto()
    data = text.encode("utf-8")
    if b"PRIVATE KEY" not in data:
        raise KeyManagementError("Das ist keine private OpenSSH-Schlüsseldatei (PuTTY-.ppk-Dateien lassen sich vorher mit PuTTYgen exportieren).")
    encrypted = (b"ENCRYPTED" in data) or _openssh_encrypted(text)
    pw = passphrase.encode("utf-8") if passphrase else None
    for loader in (serialization.load_ssh_private_key, serialization.load_pem_private_key):
        try:
            key = loader(data, password=pw)
        except TypeError:  # verschlüsselt, aber keine Passphrase angegeben
            if public_hint:
                return _material(text, public_hint, True)
            raise NeedsPassphrase("Der Schlüssel ist durch eine Passphrase geschützt.") from None
        except ValueError as exc:
            if "password" in str(exc).lower() or "decrypt" in str(exc).lower() or "bad" in str(exc).lower():
                if passphrase and not public_hint:
                    raise KeyManagementError("Die Passphrase ist falsch.") from exc
            continue
        except Exception:  # noqa: BLE001 - nicht unterstützte Schlüsselarten usw.
            continue
        public = key.public_key().public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH).decode("ascii")
        return _material(text, public, encrypted)
    if passphrase and public_hint:
        return _material(text, public_hint, True)
    if encrypted and passphrase:
        raise KeyManagementError("Die Passphrase ist falsch.")
    raise KeyManagementError("Die Schlüsseldatei konnte nicht gelesen werden (unbekanntes Format oder beschädigt).")


class NeedsPassphrase(KeyManagementError):
    """Der Schlüssel ist verschlüsselt und es liegt kein öffentlicher Schlüssel daneben."""


def _openssh_encrypted(text: str) -> bool:
    """Erkennt einen verschlüsselten Schlüssel im neuen OpenSSH-Format (Chiffrenname im Kopfteil)."""
    try:
        body = "".join(line for line in text.splitlines() if not line.startswith("-----"))
        raw = base64.b64decode(body)
    except ValueError:
        return False
    if not raw.startswith(b"openssh-key-v1\x00"):
        return False
    pos = len(b"openssh-key-v1\x00")
    length = int.from_bytes(raw[pos:pos + 4], "big")
    return raw[pos + 4:pos + 4 + length] != b"none"


def _material(private: str, public: str, encrypted: bool) -> KeyMaterial:
    public = " ".join(public.split()[:2])
    return KeyMaterial(private if private.endswith("\n") else private + "\n", public, type_of(public), fingerprint(public), encrypted)


def read_key_file(path: str | Path, passphrase: str | None = None) -> KeyMaterial:
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise KeyManagementError(f"Datei konnte nicht gelesen werden: {exc}") from exc
    hint = None
    pub = path.with_name(path.name + ".pub")
    if pub.is_file():
        try:
            hint = pub.read_text(encoding="utf-8").strip().splitlines()[0]
            fingerprint(hint)
        except (OSError, IndexError, KeyManagementError):
            hint = None
    return read_key(text, passphrase, hint)


def new_entry(name: str, material: KeyMaterial, comment: str = "") -> SshKey:
    """Legt den Konfigurationseintrag zu einem Schlüssel an (der private Teil kommt in den Dateispeicher)."""
    return SshKey(
        name=name.strip(), type=material.type, public_key=material.public, comment=comment.strip(),
        fingerprint=material.fingerprint, encrypted=material.encrypted, created=date.today().isoformat(),
        use_password=True,
    )


def users_of(cfg: Config, key_id: str) -> list[str]:
    """Namen der Verbindungen und Identitäten, die den Schlüssel benutzen."""
    return [c.name for c in cfg.connections if c.key_id == key_id] + [f"Identität {i.name}" for i in cfg.identities if i.key_id == key_id]


def forget(cfg: Config, key_id: str) -> None:
    """Entfernt den Schlüssel aus Konfiguration und allen Zuweisungen (der Dateispeicher wird vom Aufrufer bereinigt)."""
    cfg.ssh_keys = [k for k in cfg.ssh_keys if k.id != key_id]
    for obj in [*cfg.connections, *cfg.identities]:
        if obj.key_id == key_id:
            obj.key_id = ""


INSTALL_SCRIPT = (
    "umask 077; mkdir -p ~/.ssh && touch ~/.ssh/authorized_keys && chmod 700 ~/.ssh && chmod 600 ~/.ssh/authorized_keys && "
    "if grep -qxF {line} ~/.ssh/authorized_keys; then echo SESAM_PRESENT; else "
    "{{ [ ! -s ~/.ssh/authorized_keys ] || [ -z \"$(tail -c1 ~/.ssh/authorized_keys)\" ] || echo >> ~/.ssh/authorized_keys; }} && "
    "printf '%s\\n' {line} >> ~/.ssh/authorized_keys && echo SESAM_ADDED; fi"
)


def install_public_key(
    cfg: Config, conn: Connection, authorized_line: str, password: str | None = None, known_hosts: Path | None = None,
    timeout: float = 10.0,
) -> bool:
    """Trägt den öffentlichen Schlüssel in ``~/.ssh/authorized_keys`` des Hosts ein (wie ``ssh-copy-id``).

    Liefert True, wenn er neu eingetragen wurde, False, wenn er schon vorhanden war.
    Blockiert, gehört in einen Hintergrund-Thread. Wirft ``sftp.SftpAuthError``/``sftp.SftpError`` oder ``KeyManagementError``.
    """
    line = " ".join(authorized_line.split())
    if "\n" in authorized_line.strip() or len(line.split()) < 2:
        raise KeyManagementError("Kein gültiger öffentlicher Schlüssel.")
    fingerprint(line)
    client, _eff = sftp.open_client(cfg, conn, password=password, known_hosts=known_hosts, timeout=timeout,
                                    purpose="die Schlüssel-Installation")
    try:
        try:
            _in, out, err = client.exec_command(INSTALL_SCRIPT.format(line=shlex.quote(line)), timeout=20)
            stdout = out.read().decode("utf-8", "replace")
            stderr = err.read().decode("utf-8", "replace")
            status = out.channel.recv_exit_status()
        except (OSError, EOFError, sftp._paramiko().SSHException) as exc:
            raise KeyManagementError(f"Installation fehlgeschlagen: {exc}") from exc
    finally:
        client.close()
    if status != 0 or ("SESAM_ADDED" not in stdout and "SESAM_PRESENT" not in stdout):
        detail = (stderr.strip().splitlines() or [f"Fehlercode {status}"])[0]
        raise KeyManagementError(f"Installation fehlgeschlagen: {detail}")
    return "SESAM_ADDED" in stdout
