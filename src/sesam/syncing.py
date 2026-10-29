"""Synchronisation von Verbindungen, Identitäten und Gruppen über ein Git-Repository.

Aufbau des Repositories (ein Eintrag pro Datei, damit Änderungen sauber zusammengeführt werden):
    connections/<id>.json   identities/<id>.json   ssh_keys/<id>.json   snippets/<id>.json   groups.json   meta.json

Passwörter werden nie im Klartext abgelegt, sondern mit einem Sync-Passwort (scrypt + AES-256-GCM)
verschlüsselt im jeweiligen Eintrag (``password_enc``). Das lokale Git-Verzeichnis ist nur ein Zwischenspeicher
und wird bei jedem Abgleich auf den Stand des Servers gesetzt.

Zusammenführung: Dreiwege-Vergleich je Eintrag (lokal / Server / Stand des letzten erfolgreichen Abgleichs).
Löschungen werden übernommen; wurde derselbe Eintrag auf beiden Seiten verschieden geändert, ist das ein Konflikt:
merge() fragt dann über den Rückruf ``resolve`` je Eintrag, ob die lokale oder die Server-Version gelten soll.
Die lokale Version wird danach erneut auf den Server hochgeladen, die Server-Version ersetzt die lokale.
Ohne Rückruf (z. B. ohne Oberfläche) bleibt die lokale Version.

Ablauf: fetch() (Netzwerk) -> merge() (lokal, verändert die Konfiguration) -> push() (Netzwerk).
"""
from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import json
import os
import re
import socket
import subprocess
import sys
from dataclasses import asdict, dataclass, field, fields, replace
from datetime import datetime
from pathlib import Path
from typing import Callable, Protocol

from . import duplicates, keystore, passwords, runtime, storage
from .models import Config, Connection, Identity, Snippet, SshKey, SyncSettings
from .i18n import tr

PASSPHRASE_KEY = "sync:passphrase"  # Schlüssel im Schlüsselbund für das Sync-Passwort
GIT_PASSWORD_KEY = "sync:git-password"  # Schlüssel im Schlüsselbund für das Repository-Passwort bzw. den Token
FORMAT = 1
KINDS = {"c": ("connections", Connection), "i": ("identities", Identity), "k": ("ssh_keys", SshKey), "s": ("snippets", Snippet)}
VOLATILE = {"use_password"}  # lokal abgeleitet, nicht Teil des Vergleichs
SAFE_ID = keystore._SAFE_ID  # IDs werden zu Dateinamen im Repository: keine Pfadtrenner, kein „..“
_SAFE_BRANCH = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._/-]{0,199}")
_UNSAFE_URL = re.compile(r"[\x00-\x20\x7f]")
SCRYPT_LIMITS = {"n": (2, 2**18), "r": (1, 16), "p": (1, 8)}  # Obergrenzen gegen manipulierte meta.json (Speicher und Rechenzeit)


def check_branch(branch: str) -> str:
    """Branch-Name, der nie wie eine git-Option aussieht."""
    if not _SAFE_BRANCH.fullmatch(branch) or ".." in branch or branch.endswith(("/", ".lock")):
        raise SyncError(tr("Ungültiger Branch-Name: {branch}", branch=repr(branch)))
    return branch


def check_url(url: str) -> str:
    """Repository-Adresse, die nie wie eine git-Option aussieht (``--upload-pack=…``) und keine Leer- oder Steuerzeichen enthält."""
    url = url.strip()
    if url.startswith("-") or _UNSAFE_URL.search(url):
        raise SyncError(tr("Ungültige Repository-Adresse."))
    return url


class SyncError(Exception):
    def __init__(self, message: str = "", detail: str = ""):
        super().__init__(message)
        self.detail = detail  # komplette Ausgabe von git (für die Fehlererkennung)


class SyncRetry(SyncError):
    """Der Server hat sich zwischenzeitlich geändert; der Abgleich muss neu beginnen."""


# ---------------------------------------------------------------- Passwortspeicher

class PasswordStore(Protocol):
    def get(self, key: str) -> str | None: ...
    def set(self, key: str, value: str) -> None: ...
    def delete(self, key: str) -> None: ...


class KeyringStore:
    def get(self, key):
        return passwords.get_password(key)

    def set(self, key, value):
        passwords.set_password(key, value)

    def delete(self, key):
        passwords.delete_password(key)


# ---------------------------------------------------------------- Kryptografie

def _b64e(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _b64d(s: str) -> bytes:
    return base64.b64decode(s.encode("ascii"))


def new_kdf() -> dict:
    return {"name": "scrypt", "n": 2**17, "r": 8, "p": 1, "salt": _b64e(os.urandom(16))}


def derive_key(passphrase: str, kdf: dict) -> bytes:
    if not isinstance(kdf, dict) or kdf.get("name") != "scrypt":
        raise SyncError(tr("Unbekanntes Schlüsselableitungsverfahren im Repository."))
    try:
        params = {k: kdf[k] for k in SCRYPT_LIMITS}
        if any(isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi for (k, v), (lo, hi) in zip(params.items(), SCRYPT_LIMITS.values())):
            raise ValueError("Parameter außerhalb des erlaubten Bereichs")
        return hashlib.scrypt(
            passphrase.encode("utf-8"), salt=_b64d(kdf["salt"]), maxmem=256 * 1024 * 1024, dklen=32, **params,
        )
    except (KeyError, ValueError, TypeError, AttributeError) as exc:
        raise SyncError(tr("Ungültige Schlüsselableitung im Repository (meta.json).")) from exc


def encrypt(key: bytes, plaintext: str, aad: str) -> dict:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    nonce = os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), aad.encode("utf-8"))
    return {"nonce": _b64e(nonce), "ct": _b64e(ct)}


def decrypt(key: bytes, blob: dict, aad: str) -> str:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    try:
        return AESGCM(key).decrypt(_b64d(blob["nonce"]), _b64d(blob["ct"]), aad.encode("utf-8")).decode("utf-8")
    except (InvalidTag, KeyError, ValueError, TypeError, AttributeError) as exc:
        raise SyncError(tr("Entschlüsselung fehlgeschlagen (falsches Sync-Passwort?).")) from exc


# ---------------------------------------------------------------- Integrität
#
# Das Passwort-Blob eines Eintrags ist per AES-GCM geschützt, alle übrigen Felder (Host, extra_args, Remote-Befehl …) liegen im
# Klartext im Repository. Ohne weiteren Schutz könnte jeder mit Schreibzugriff auf das Repository dort einen ProxyCommand eintragen
# oder den Host umbiegen. Deshalb trägt jeder Eintrag eine Signatur (HMAC-SHA256 mit einem aus dem Sync-Passwort abgeleiteten
# Schlüssel) über den gesamten Inhalt samt Art und ID. meta.json enthält eine signierte Markierung; ein Gerät, das einmal ein
# geschütztes Repository gesehen hat, nimmt danach keine unsignierten Einträge mehr an (kein Zurückstufen).
# Nicht abgedeckt: Löschen von Einträgen und das Zurückspielen einer älteren, ebenfalls gültig signierten Fassung.

def _mac_key(key: bytes) -> bytes:
    return hmac.new(key, b"sesam-entry-mac-v1", hashlib.sha256).digest()


def entry_mac(key: bytes, kind: str, content: dict) -> str:
    """Signatur über den kompletten Dateiinhalt eines Eintrags (ohne das Feld ``mac`` selbst)."""
    canonical = json.dumps(content, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hmac.new(_mac_key(key), f"entry\0{kind}\0".encode() + canonical.encode("utf-8"), hashlib.sha256).hexdigest()


def integrity_tag(key: bytes) -> str:
    """Signierte Markierung in meta.json: Das Repository schützt seine Einträge."""
    return hmac.new(_mac_key(key), b"integrity-v1", hashlib.sha256).hexdigest()


# ---------------------------------------------------------------- Einträge und Zusammenführung

@dataclass
class Item:
    kind: str  # "c" Verbindung, "i" Identität, "k" SSH-Schlüssel
    id: str
    data: dict  # ohne use_password
    pw: str | None = None
    blob: dict | None = field(default=None, compare=False)  # vorhandener verschlüsselter Block im Repository
    pw_missing: bool = field(default=False, compare=False)  # lokal als gespeichert markiert, aber nicht im Schlüsselbund
    renamed_from: str | None = field(default=None, compare=False)  # lokale ID, die auf die ID des gleichen Servereintrags umgestellt wurde
    mac: str | None = field(default=None, compare=False)  # Signatur des Eintrags im Repository (siehe entry_mac)

    @property
    def key(self) -> tuple[str, str]:
        return (self.kind, self.id)


def item_from_obj(kind: str, obj, pw: str | None) -> Item:
    data = {k: v for k, v in asdict(obj).items() if k not in VOLATILE}
    return Item(kind, obj.id, data, pw)


def item_hash(item: Item, hkey: bytes) -> str:
    canonical = json.dumps({"k": item.kind, "d": item.data, "p": item.pw}, sort_keys=True, ensure_ascii=False)
    return hmac.new(hkey or b"-", canonical.encode("utf-8"), hashlib.sha256).hexdigest()


def match_duplicates(base: dict, local: dict, remote: dict) -> dict:
    """Lokal neue Einträge, die auf dem Server schon (mit anderer ID) stehen: dieselbe Verbindung bzw. Identität, getrennt angelegt
    (z. B. Import aus ~/.ssh/config auf beiden Geräten). Liefert {lokaler Schlüssel: Schlüssel des Servereintrags}."""
    by_sig: dict = {}
    for k in sorted(remote):
        if k in local or k in base:
            continue
        sig = duplicates.signature(k[0], remote[k].data)
        if sig is not None:
            by_sig.setdefault((k[0], sig), []).append(k)
    mapping: dict = {}
    for k in sorted(local):
        if k in remote or k in base:
            continue
        sig = duplicates.signature(k[0], local[k].data)
        candidates = by_sig.get((k[0], sig)) if sig is not None else None
        if candidates:
            mapping[k] = candidates.pop(0)
    return mapping


def adopt_remote_ids(local: dict, mapping: dict) -> dict:
    """Stellt die zugeordneten lokalen Einträge auf die IDs der Servereinträge um (und Verweise auf umbenannte Identitäten)."""
    out: dict = {}
    for k, item in local.items():
        if k in mapping:
            new_id = mapping[k][1]
            out[mapping[k]] = replace(item, id=new_id, data=dict(item.data, id=new_id), blob=None, renamed_from=k[1])
        else:
            out[k] = item
    ident_ids = {k[1]: v[1] for k, v in mapping.items() if k[0] == "i"}
    if ident_ids:
        for k, item in out.items():
            if k[0] == "c" and item.data.get("identity_id") in ident_ids:
                out[k] = replace(item, data=dict(item.data, identity_id=ident_ids[item.data["identity_id"]]))
    return out


def merge_items(base: dict, local: dict, remote: dict, h: Callable[[Item], str]):
    """Dreiwege-Zusammenführung. base: key -> Hash. Liefert (Ergebnis, Konflikte[(key, lokal, remote)])."""
    result: dict = {}
    conflicts: list = []
    for key in set(local) | set(remote) | set(base):
        L, R, b = local.get(key), remote.get(key), base.get(key)
        hl = h(L) if L else None
        hr = h(R) if R else None
        if L and R:
            if hl == hr:
                result[key] = L
            elif hl == b:
                result[key] = R  # nur Server geändert
            elif hr == b:
                result[key] = L  # nur lokal geändert
            else:
                result[key] = L
                conflicts.append((key, L, R))
        elif L:
            if b is None or hl != b:
                result[key] = L  # neu lokal, oder lokal geändert und am Server gelöscht (bleibt erhalten)
            # sonst: am Server gelöscht -> entfällt
        elif R:
            if b is None or hr != b:
                result[key] = R  # neu am Server, oder dort geändert und lokal gelöscht (bleibt erhalten)
            # sonst: lokal gelöscht -> entfällt
    return result, conflicts


LOCAL, REMOTE = "local", "remote"


@dataclass
class Conflict:
    """Ein Eintrag, der lokal und auf dem Server unterschiedlich geändert wurde."""

    key: tuple[str, str]
    local: Item
    remote: Item
    token: str  # identifiziert genau diese Konstellation (Eintrag und beide Stände)

    @property
    def kind(self) -> str:
        return self.key[0]

    @property
    def name(self) -> str:
        return str(self.local.data.get("name") or self.remote.data.get("name") or self.key[1])

    def differences(self) -> list[tuple[str, object, object]]:
        """(Feld, lokaler Wert, Server-Wert) der unterschiedlichen Felder; Passwörter nur als Feld ohne Werte."""
        out: list[tuple[str, object, object]] = []
        for f in sorted(set(self.local.data) | set(self.remote.data)):
            lv, rv = self.local.data.get(f), self.remote.data.get(f)
            if lv != rv:
                out.append((f, lv, rv))
        if self.local.pw != self.remote.pw:
            out.append(("password", None, None))
        return out


def merge_sets(base: set, local: set, remote: set) -> set:
    return (base | (local - base) | (remote - base)) - (base - local) - (base - remote)


def merge_map(base: dict, local: dict, remote: dict) -> dict:
    """Drei-Wege-Zusammenführung je Schlüssel; bei widersprüchlichen Änderungen gewinnt die lokale."""
    out = {}
    for k in set(base) | set(local) | set(remote):
        b, loc, rem = base.get(k), local.get(k), remote.get(k)
        value = rem if loc == b else loc  # nur eine Seite geändert -> diese; beide -> lokal
        if value is not None:
            out[k] = value
    return out


# ---------------------------------------------------------------- Git

# Credential-Helper für HTTPS: antwortet nur auf „get“ und liest Benutzer und Passwort aus der Umgebung des
# git-Prozesses. So tauchen die Zugangsdaten weder in der Befehlszeile noch in der URL oder in .git/config auf.
CREDENTIAL_HELPER = '!f() { test "$1" = get && printf "username=%s\\npassword=%s\\n" "$SESAM_GIT_USER" "$SESAM_GIT_PASSWORD"; }; f'


def credential_config(credentials: tuple[str, str] | None) -> tuple[list[str], dict[str, str]]:
    """git-Optionen und Umgebungsvariablen, mit denen git die Zugangsdaten für HTTPS-Repositories bekommt."""
    if not credentials:
        return [], {}
    user, password = credentials
    args = ["-c", "credential.helper=", "-c", f"credential.helper={CREDENTIAL_HELPER}"]  # erste Zeile leert vorhandene Helfer
    return args, {"SESAM_GIT_USER": user, "SESAM_GIT_PASSWORD": password}


def load_credentials(settings: SyncSettings) -> tuple[str, str] | None:
    """Benutzername aus den Einstellungen, Passwort bzw. Token aus dem Schlüsselbund. None ohne Benutzernamen."""
    user = settings.username.strip()
    if not user:
        return None
    if settings.url.strip().lower().startswith("http://"):
        raise SyncError(tr("Zugangsdaten werden nicht über unverschlüsseltes HTTP gesendet. Bitte eine https://-Adresse verwenden."))
    password = ""
    if settings.git_password_saved:
        try:
            password = passwords.get_password(GIT_PASSWORD_KEY) or ""
        except passwords.PasswordError as exc:
            raise SyncError(str(exc)) from exc
    return (user, password)


class Git:
    def __init__(self, repo: Path, credentials: tuple[str, str] | None = None):
        self.repo = repo
        self.credentials = credentials

    def run(self, *args: str, check: bool = True, timeout: int = 120) -> str:
        env = runtime.clean_env()
        env.setdefault("GIT_TERMINAL_PROMPT", "0")
        env.setdefault("GIT_SSH_COMMAND", "ssh -o BatchMode=yes")
        env.setdefault("GIT_ALLOW_PROTOCOL", "https:http:ssh:git:file")  # keine Hilfsprogramm-Transporte wie „ext::“
        cred_args, cred_env = credential_config(self.credentials)
        env.update(cred_env)
        kwargs = {}
        if sys.platform.startswith("win"):
            kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
        try:
            proc = subprocess.run(
                ["git", *cred_args, *args], cwd=self.repo, env=env, capture_output=True, text=True, timeout=timeout,
                encoding="utf-8", errors="replace", **kwargs,
            )
        except FileNotFoundError as exc:
            raise SyncError(tr("git wurde nicht gefunden. Bitte Git installieren.")) from exc
        except subprocess.TimeoutExpired as exc:
            raise SyncError(tr("Zeitüberschreitung bei „git {v}“.", v=args[0])) from exc
        if check and proc.returncode != 0:
            msg = (proc.stderr or proc.stdout).strip().splitlines()
            short = next((m for m in reversed(msg) if not m.startswith("hint:")), msg[-1] if msg else str(proc.returncode))
            raise SyncError(tr("git {v} fehlgeschlagen: {short}", v=args[0], short=short), detail="\n".join(msg))
        return proc.stdout


def check_remote(url: str, credentials: tuple[str, str] | None = None) -> str:
    """Prüft, ob das Repository erreichbar ist. Liefert eine kurze Beschreibung."""
    url = check_url(url)
    if credentials and url.lower().startswith("http://"):
        raise SyncError(tr("Zugangsdaten werden nicht über unverschlüsseltes HTTP gesendet. Bitte eine https://-Adresse verwenden."))
    out = Git(Path.cwd(), credentials).run("ls-remote", "--heads", url, timeout=60)
    n = len(out.strip().splitlines())
    return tr("Repository erreichbar (leer).") if n == 0 else tr("Repository erreichbar ({n} Branch(es)).", n=n)


# ---------------------------------------------------------------- Engine

@dataclass
class RemoteState:
    items: dict
    groups: set
    meta: dict | None
    key: bytes | None
    branch_exists: bool
    new_meta: bool = False  # meta.json muss (neu) geschrieben werden
    group_colors: dict = field(default_factory=dict)
    rejected: dict = field(default_factory=dict)  # Schlüssel -> Name der Einträge ohne gültige Signatur (nicht übernommen)


@dataclass
class SyncReport:
    added: int = 0
    updated: int = 0
    removed: int = 0
    merged: int = 0  # doppelte Einträge (gleiche Verbindung mit verschiedenen IDs), die zusammengeführt wurden
    conflicts: list[str] = field(default_factory=list)
    kept_remote: list[str] = field(default_factory=list)  # Konflikte, bei denen die Server-Version gewählt wurde
    rejected: list[str] = field(default_factory=list)  # Einträge im Repository ohne gültige Signatur (abgelehnt, lokale Fassung bleibt)
    local_changed: bool = False
    committed: bool = False
    pushed: bool = False

    def summary(self) -> str:
        parts = []
        if self.added or self.updated or self.removed:
            parts.append(tr("lokal: {added} neu, {updated} geändert, {removed} entfernt", added=self.added, updated=self.updated, removed=self.removed))
        if self.merged:
            parts.append(tr("{n} doppelte(r) Eintrag/Einträge zusammengeführt", n=self.merged))
        if self.pushed:
            parts.append(tr("Änderungen hochgeladen"))
        if self.rejected:
            parts.append(tr("{n} Eintrag/Einträge im Repository abgelehnt (nicht mit dem Sync-Passwort signiert): ", n=len(self.rejected))
                         + ", ".join(self.rejected))
        if self.conflicts:
            parts.append(tr("{n} Konflikt(e): ", n=len(self.conflicts)) + ", ".join(self.conflicts))
        return "; ".join(parts) or tr("Alles aktuell")


class SyncEngine:
    def __init__(
        self,
        settings: SyncSettings,
        work_dir: Path,
        passphrase: str | None = None,
        store: PasswordStore | None = None,
        device: str | None = None,
        key_store: PasswordStore | None = None,
        credentials: tuple[str, str] | None = None,
    ):
        self.s = settings
        self.work = Path(work_dir)
        self.repo = self.work / "repo"
        self.state_path = self.work / "state.json"
        self.passphrase = passphrase
        self.store = store or KeyringStore()
        # private SSH-Schlüssel: eigene Dateien statt Schlüsselbund (außer ein Test gibt nur einen gemeinsamen Speicher vor)
        self.key_store = key_store or (keystore.FileStore() if store is None else store)
        self.device = device or socket.gethostname()
        self.git = Git(self.repo, credentials)
        self._remote: RemoteState | None = None
        self._next_state: dict | None = None
        self._dirty_commit = False

    # ---- Zustand ----
    def _load_state(self) -> dict:
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _integrity_expected(self) -> bool:
        """Hat dieses Gerät dieses Repository schon einmal mit Signaturen gesehen? Dann sind unsignierte Einträge nie mehr gültig."""
        return self._load_state().get("integrity") == self.s.url.strip()

    def _save_state(self, state: dict) -> None:
        self.work.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        os.replace(tmp, self.state_path)

    # ---- Phase 1: Server-Stand holen ----
    def fetch(self) -> RemoteState:
        if not self.s.url.strip():
            raise SyncError(tr("Keine Repository-URL eingestellt."))
        url = check_url(self.s.url)
        branch = check_branch(self.s.branch.strip() or "main")
        self.repo.parent.mkdir(parents=True, exist_ok=True)
        if not (self.repo / ".git").exists():
            self.repo.mkdir(parents=True, exist_ok=True)
            Git(self.repo).run("init", "-q")
            self.git.run("remote", "add", "origin", url)
        else:
            self.git.run("remote", "set-url", "origin", url)
        self.git.run("config", "core.symlinks", "false")  # ein feindliches Repository darf keine Links ins Dateisystem legen
        heads = self.git.run("ls-remote", "--heads", "origin", branch, timeout=60).strip()
        exists = bool(heads)
        if exists:
            self.git.run("fetch", "-q", "origin", branch, timeout=180)
            self.git.run("checkout", "-q", "-f", "-B", branch, "FETCH_HEAD")
            self.git.run("reset", "-q", "--hard", "FETCH_HEAD")
        else:
            self.git.run("symbolic-ref", "HEAD", f"refs/heads/{branch}")
            self.git.run("rm", "-rq", "--cached", ".", check=False)
        self.git.run("clean", "-fdxq")
        self._reject_symlinks()
        self._remote = self._read_remote(exists)
        return self._remote

    def _reject_symlinks(self) -> None:
        for root, dirs, files in os.walk(self.repo):
            if ".git" in dirs:
                dirs.remove(".git")
            for name in (*dirs, *files):
                if os.path.islink(os.path.join(root, name)):
                    raise SyncError(tr("Das Repository enthält symbolische Links und wird aus Sicherheitsgründen nicht verwendet."))

    def _read_remote(self, branch_exists: bool) -> RemoteState:
        meta = None
        meta_path = self.repo / "meta.json"
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (ValueError, OSError) as exc:  # ValueError: auch ungültiges UTF-8
                raise SyncError(tr("meta.json im Repository ist beschädigt.")) from exc
            if not isinstance(meta, dict):
                raise SyncError(tr("meta.json im Repository ist beschädigt."))
        key, new_meta = None, False
        enforce = self._integrity_expected()
        if self.s.passwords:
            if not self.passphrase:
                raise SyncError(tr("Das Sync-Passwort fehlt (Seitenleiste → Synchronisation …)."))
            if meta and meta.get("kdf"):
                key = derive_key(self.passphrase, meta["kdf"])
                decrypt(key, meta.get("check"), "check")  # prüft das Sync-Passwort
                marker = meta.get("integrity")
                tag = marker.get("tag") if isinstance(marker, dict) else None
                if tag is None:
                    if enforce:
                        raise SyncError(tr("Im Repository fehlt der Integritätsschutz, den dieses Gerät schon kennt (meta.json verändert?). Der Abgleich wird aus Sicherheitsgründen nicht fortgesetzt."))
                    meta["integrity"] = {"v": 1, "tag": integrity_tag(key)}  # älteres Repository: beim ersten Abgleich nachrüsten
                    new_meta = True
                elif isinstance(tag, str) and hmac.compare_digest(tag, integrity_tag(key)):
                    enforce = True
                else:
                    raise SyncError(tr("meta.json im Repository wurde verändert (Integritätsprüfung fehlgeschlagen)."))
            else:
                meta = {"format": FORMAT, "kdf": new_kdf()}
                key = derive_key(self.passphrase, meta["kdf"])
                meta["check"] = encrypt(key, "ssh-verwaltung", "check")  # Prüftext, bewusst unverändert (Kompatibilität)
                meta["integrity"] = {"v": 1, "tag": integrity_tag(key)}
                new_meta = True
        items: dict = {}
        rejected: dict = {}
        for kind, (folder, _cls) in KINDS.items():
            for path in sorted((self.repo / folder).glob("*.json")) if (self.repo / folder).is_dir() else []:
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    if not isinstance(data, dict):
                        continue
                    mac = data.pop("mac", None)
                    item_id = data["id"]
                    if not isinstance(item_id, str) or not SAFE_ID.fullmatch(item_id):
                        continue  # die ID wird zum Dateinamen: nichts aus einem manipulierten Repository übernehmen
                    if key is not None:
                        valid = isinstance(mac, str) and hmac.compare_digest(mac, entry_mac(key, kind, data))
                        if not valid and (enforce or mac is not None):  # unsigniert ist nur in einem noch ungeschützten Repository erlaubt
                            rejected[(kind, item_id)] = str(data.get("name") or item_id)
                            continue
                    blob = data.pop("password_enc", None)
                    # auf bekannte Felder normalisieren (fehlende Felder bekommen Standardwerte)
                    data = {k: v for k, v in asdict(_cls.from_dict(data)).items() if k not in VOLATILE}
                except (ValueError, KeyError, OSError, TypeError, AttributeError, RecursionError):
                    continue  # beschädigte Datei überspringen
                pw = decrypt(key, blob, item_id) if (key and blob) else None
                items[(kind, item_id)] = Item(kind, item_id, data, pw, blob, mac=mac if isinstance(mac, str) else None)
        groups: set = set()
        colors: dict = {}
        gp = self.repo / "groups.json"
        if gp.exists():
            try:
                gdata = json.loads(gp.read_text(encoding="utf-8"))
                if isinstance(gdata, dict):
                    listed = gdata.get("groups", [])
                    groups = {g for g in listed if isinstance(g, str)} if isinstance(listed, list) else set()
                    raw = gdata.get("colors")
                    if isinstance(raw, dict):
                        colors = {k: v for k, v in raw.items() if isinstance(k, str) and isinstance(v, str) and v}
            except (ValueError, OSError, RecursionError):
                pass
        return RemoteState(items, groups, meta, key, branch_exists, new_meta, colors, rejected)

    # ---- Phase 2: zusammenführen (verändert cfg, Schlüsselbund und Arbeitsverzeichnis) ----
    def _store_for(self, kind: str) -> PasswordStore:
        return self.key_store if kind == "k" else self.store

    def _local_items(self, cfg: Config, key: bytes | None) -> dict:
        items: dict = {}
        for kind, objs in (("c", cfg.connections), ("i", cfg.identities), ("k", cfg.ssh_keys), ("s", cfg.snippets)):
            for o in objs:
                pw = None
                if key is not None and o.use_password:
                    try:
                        pw = self._store_for(kind).get(o.id)
                    except passwords.PasswordError as exc:
                        raise SyncError(str(exc)) from exc
                item = item_from_obj(kind, o, pw)
                item.pw_missing = bool(key is not None and o.use_password and pw is None)
                items[(kind, o.id)] = item
        return items

    def merge(
        self,
        cfg: Config,
        remote: RemoteState | None = None,
        resolve: Callable[[list[Conflict]], dict[str, str] | None] | None = None,
    ) -> SyncReport:
        """Führt lokal und Server zusammen. ``resolve`` bekommt die Konflikte und liefert je ``Conflict.token``
        ``LOCAL`` oder ``REMOTE`` (fehlend = lokal) oder ``None`` zum Abbrechen."""
        remote = remote or self._remote
        if remote is None:
            raise SyncError("fetch() muss vor merge() laufen.")
        key = remote.key
        hkey = key or b""
        mode = hashlib.sha256(key).hexdigest()[:12] if key else "off"
        state = self._load_state()
        if state.get("mode") != mode:
            state = {}  # Hash-Verfahren geändert: neu beginnen, Löschungen werden dann nicht übertragen
        base = state.get("items", {})
        base_key = {tuple(k.split(":", 1)): v for k, v in base.items()}
        base_groups = set(state.get("groups", []))
        base_colors = state.get("group_colors", {}) if isinstance(state.get("group_colors"), dict) else {}

        before = copy.deepcopy(asdict(cfg))
        local = self._local_items(cfg, key)
        ritems = remote.items
        if remote.rejected:
            # Ein Eintrag ohne gültige Signatur zählt nicht als Änderung und nicht als Löschung am Server: Die lokale Fassung bleibt und
            # überschreibt die Datei beim Hochladen (mit gültiger Signatur). Gibt es lokal keine, verschwindet die Datei aus dem Repository.
            ritems = dict(remote.items)
            for rk in remote.rejected:
                if rk in local:
                    ritems[rk] = replace(local[rk], blob=None, renamed_from=None, pw_missing=False, mac=None)
        local = adopt_remote_ids(local, match_duplicates(base_key, local, ritems))
        for k, it in local.items():
            # Passwort fehlt lokal im Schlüsselbund: nicht als „gelöscht“ auf den Server übertragen, sondern wiederherstellen
            if it.pw_missing and k in ritems and ritems[k].pw is not None:
                it.pw = ritems[k].pw
        result, raw_conflicts = merge_items(base_key, local, ritems, lambda it: item_hash(it, hkey))
        report = SyncReport()
        report.rejected = sorted(remote.rejected.values(), key=str.lower)
        conflicts = [
            Conflict(k, loc, rem, f"{k[0]}:{k[1]}:{item_hash(loc, hkey)}:{item_hash(rem, hkey)}")
            for k, loc, rem in raw_conflicts
        ]
        choices: dict[str, str] = {}
        if conflicts and resolve is not None:
            answer = resolve(conflicts)
            if answer is None:
                raise SyncError(tr("Abgleich abgebrochen."))
            choices = answer
        for c in sorted(conflicts, key=lambda c: c.name.lower()):
            report.conflicts.append(c.name)
            if choices.get(c.token, LOCAL) == REMOTE:
                result[c.key] = c.remote  # Server-Version übernehmen; lokal wird sie durch _apply_local ersetzt
                report.kept_remote.append(c.name)
            # sonst bleibt die lokale Version im Ergebnis und geht beim Hochladen an den Server

        merged_groups = merge_sets(base_groups, set(cfg.groups), remote.groups)
        merged_colors = merge_map(base_colors, cfg.group_colors, remote.group_colors)

        self._apply_local(cfg, local, result, key is not None, report)
        cfg.groups = sorted(merged_groups, key=str.lower)
        cfg.group_colors = merged_colors
        report.local_changed = asdict(cfg) != before

        self._write_repo(result, merged_groups, remote, key, merged_colors)
        status = self.git.run("status", "--porcelain")
        if status.strip():
            self.git.run("add", "-A")
            n = len(status.strip().splitlines())
            self.git.run(
                "-c", "user.name=Sesam", "-c", "user.email=sesam@localhost",
                "commit", "-q", "-m", f"Sync von {self.device}: {n} Datei(en)",
            )
            report.committed = True
        self._dirty_commit = report.committed
        self._next_state = {
            "mode": mode,
            "items": {f"{k[0]}:{k[1]}": item_hash(it, hkey) for k, it in result.items()},
            "groups": sorted(merged_groups),
            "group_colors": merged_colors,
            "integrity": self.s.url.strip() if key is not None else self._load_state().get("integrity"),
        }
        return report

    def _apply_local(self, cfg: Config, local: dict, result: dict, pw_on: bool, report: SyncReport) -> None:
        old = {k: o for kind, objs in (("c", cfg.connections), ("i", cfg.identities), ("k", cfg.ssh_keys), ("s", cfg.snippets))
               for o in objs for k in [(kind, o.id)]}
        new_conns: list[Connection] = []
        new_idents: list[Identity] = []
        new_keys: list[SshKey] = []
        new_snippets: list[Snippet] = []
        moved_old = {(k[0], it.renamed_from) for k, it in local.items() if it.renamed_from}
        for k, item in result.items():
            _folder, cls = KINDS[k[0]]
            obj = cls.from_dict(dict(item.data, id=item.id))
            existing = old.get(k)
            moved_from = local[k].renamed_from if k in local else None
            moved_obj = old.get((k[0], moved_from)) if moved_from else None
            if moved_obj is not None and moved_obj.use_password:  # Passwort wandert zur neuen ID; unter der alten wird es unten gelöscht
                try:
                    secret = self._store_for(k[0]).get(moved_from)
                    if secret is not None:
                        self._store_for(k[0]).set(item.id, secret)
                except passwords.PasswordError as exc:
                    raise SyncError(str(exc)) from exc
            if pw_on:
                local_pw = local[k].pw if k in local else None
                if item.pw != local_pw or (k in local and local[k].pw_missing and item.pw is not None):
                    try:
                        if item.pw is None:
                            self._store_for(k[0]).delete(item.id)
                        else:
                            self._store_for(k[0]).set(item.id, item.pw)
                    except passwords.PasswordError as exc:
                        raise SyncError(str(exc)) from exc
                obj.use_password = item.pw is not None
            else:
                source = existing or moved_obj
                obj.use_password = bool(source.use_password) if source else False
            if existing is None:
                if moved_obj is not None:
                    report.merged += 1
                else:
                    report.added += 1
            else:
                if item_from_obj(k[0], existing, None).data != item.data or (pw_on and local[k].pw != item.pw):
                    report.updated += 1
                for f in fields(obj):  # in place, damit offene Dialoge gültige Objekte behalten
                    setattr(existing, f.name, getattr(obj, f.name))
                obj = existing
            {"c": new_conns, "i": new_idents, "k": new_keys, "s": new_snippets}[k[0]].append(obj)
        for k, o in old.items():
            if k not in result:
                if k not in moved_old:
                    report.removed += 1
                if o.use_password and (pw_on or k in moved_old):
                    try:
                        self._store_for(k[0]).delete(o.id)
                    except passwords.PasswordError as exc:
                        raise SyncError(str(exc)) from exc
        ident_ids = {i.id for i in new_idents}
        key_ids = {k.id for k in new_keys}
        for c in new_conns:
            if c.identity_id and c.identity_id not in ident_ids:
                c.identity_id = ""
        for o in (*new_conns, *new_idents):
            if o.key_id and o.key_id not in key_ids:
                o.key_id = ""
        cfg.connections = sorted(new_conns, key=lambda c: (c.group.lower(), c.name.lower()))
        cfg.identities = sorted(new_idents, key=lambda i: i.name.lower())
        cfg.ssh_keys = sorted(new_keys, key=lambda k: k.name.lower())
        cfg.snippets = sorted(new_snippets, key=lambda sn: (sn.category.lower(), sn.name.lower()))

    def _write_repo(self, result: dict, groups: set, remote: RemoteState, key: bytes | None, colors: dict | None = None) -> None:
        colors = colors or {}
        def dump(obj) -> str:
            return json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n"

        def write(path: Path, text: str) -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists() or path.read_text(encoding="utf-8") != text:
                path.write_text(text, encoding="utf-8", newline="\n")

        wanted: set[Path] = set()
        for k, item in result.items():
            if not isinstance(item.id, str) or not SAFE_ID.fullmatch(item.id):
                raise SyncError(tr("Ungültige ID im Eintrag „{name}“.", name=str(item.data.get("name", ""))))
            folder = KINDS[k[0]][0]
            path = self.repo / folder / f"{item.id}.json"
            wanted.add(path)
            content = dict(item.data)
            blob = None
            if key is not None:
                if item.pw is not None:
                    old = remote.items.get(k)
                    # vorhandenen Block weiterverwenden, wenn das Passwort gleich blieb (vermeidet unnötige Änderungen)
                    blob = old.blob if (old and old.blob and old.pw == item.pw) else encrypt(key, item.pw, item.id)
            else:
                old = remote.items.get(k)
                blob = old.blob if old else None  # ohne Passwort-Sync vorhandene Blöcke erhalten
            if blob:
                content["password_enc"] = blob
            if key is not None:
                content["mac"] = entry_mac(key, k[0], content)
            else:  # ohne Sync-Passwort lässt sich nichts signieren: eine unveränderte Signatur bleibt erhalten
                old = remote.items.get(k)
                if old and old.mac and old.data == item.data and old.blob == blob:
                    content["mac"] = old.mac
            write(path, dump(content))
        for folder, _ in KINDS.values():
            d = self.repo / folder
            if d.is_dir():
                for p in d.glob("*.json"):
                    if p not in wanted:
                        p.unlink()
        content = {"groups": sorted(groups, key=str.lower)}
        if colors:  # ohne Farben bleibt die Datei im bisherigen Format
            content["colors"] = dict(sorted(colors.items()))
        write(self.repo / "groups.json", dump(content))
        if remote.new_meta and remote.meta:
            write(self.repo / "meta.json", dump(remote.meta))

    # ---- Phase 3: hochladen ----
    def push(self) -> bool:
        pushed = False
        if self._dirty_commit:
            branch = check_branch(self.s.branch.strip() or "main")
            try:
                self.git.run("push", "-q", "origin", f"HEAD:refs/heads/{branch}", timeout=180)
            except SyncError as exc:
                if any(w in (str(exc) + exc.detail).lower() for w in ("rejected", "non-fast-forward", "fetch first", "stale")):
                    raise SyncRetry(tr("Der Server wurde zwischenzeitlich geändert.")) from exc
                raise
            pushed = True
        if self._next_state is not None:
            self._save_state(self._next_state)
        self._dirty_commit = False
        self._next_state = None
        return pushed

    def run(self, cfg: Config, retries: int = 3, resolve=None) -> SyncReport:
        """Kompletter Abgleich in einem Aufruf (blockierend)."""
        for attempt in range(retries):
            remote = self.fetch()
            report = self.merge(cfg, remote, resolve)
            try:
                report.pushed = self.push()
                return report
            except SyncRetry:
                if attempt == retries - 1:
                    raise
        raise SyncError(tr("Abgleich nicht möglich."))  # pragma: no cover


def work_dir() -> Path:
    return storage.config_dir() / "sync"


def stamp() -> str:
    return datetime.now().strftime("%d.%m.%Y %H:%M")
