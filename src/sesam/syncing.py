"""Synchronisation von Verbindungen, Identitäten und Gruppen über ein Git-Repository.

Aufbau des Repositories (ein Eintrag pro Datei, damit Änderungen sauber zusammengeführt werden):
    connections/<id>.json   identities/<id>.json   ssh_keys/<id>.json   snippets/<id>.json   groups.json   meta.json

Passwörter werden nie im Klartext abgelegt, sondern mit einem Sync-Passwort (scrypt + AES-256-GCM)
verschlüsselt im jeweiligen Eintrag (``password_enc``). Das lokale Git-Verzeichnis ist nur ein Zwischenspeicher
und wird bei jedem Abgleich auf den Stand des Servers gesetzt.

Zusammenführung: Dreiwege-Vergleich je Eintrag (lokal / Server / Stand des letzten erfolgreichen Abgleichs).
Löschungen werden übernommen; wurde derselbe Eintrag auf beiden Seiten verschieden geändert, gewinnt die lokale
Version und die Server-Version bleibt als „… (Konflikt)“ erhalten.

Ablauf: fetch() (Netzwerk) -> merge() (lokal, verändert die Konfiguration) -> push() (Netzwerk).
"""
from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import json
import os
import socket
import subprocess
import sys
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from pathlib import Path
from typing import Callable, Protocol

from . import keystore, passwords, runtime, storage
from .models import Config, Connection, Identity, Snippet, SshKey, SyncSettings, new_id
from .i18n import tr

PASSPHRASE_KEY = "sync:passphrase"  # Schlüssel im Schlüsselbund für das Sync-Passwort
GIT_PASSWORD_KEY = "sync:git-password"  # Schlüssel im Schlüsselbund für das Repository-Passwort bzw. den Token
FORMAT = 1
KINDS = {"c": ("connections", Connection), "i": ("identities", Identity), "k": ("ssh_keys", SshKey), "s": ("snippets", Snippet)}
VOLATILE = {"use_password"}  # lokal abgeleitet, nicht Teil des Vergleichs


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
    return {"name": "scrypt", "n": 2**15, "r": 8, "p": 1, "salt": _b64e(os.urandom(16))}


def derive_key(passphrase: str, kdf: dict) -> bytes:
    if kdf.get("name") != "scrypt":
        raise SyncError(tr("Unbekanntes Schlüsselableitungsverfahren im Repository."))
    return hashlib.scrypt(
        passphrase.encode("utf-8"), salt=_b64d(kdf["salt"]), n=kdf["n"], r=kdf["r"], p=kdf["p"],
        maxmem=256 * 1024 * 1024, dklen=32,
    )


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
    except (InvalidTag, KeyError, ValueError) as exc:
        raise SyncError(tr("Entschlüsselung fehlgeschlagen (falsches Sync-Passwort?).")) from exc


# ---------------------------------------------------------------- Einträge und Zusammenführung

@dataclass
class Item:
    kind: str  # "c" Verbindung, "i" Identität, "k" SSH-Schlüssel
    id: str
    data: dict  # ohne use_password
    pw: str | None = None
    blob: dict | None = field(default=None, compare=False)  # vorhandener verschlüsselter Block im Repository
    pw_missing: bool = field(default=False, compare=False)  # lokal als gespeichert markiert, aber nicht im Schlüsselbund

    @property
    def key(self) -> tuple[str, str]:
        return (self.kind, self.id)


def item_from_obj(kind: str, obj, pw: str | None) -> Item:
    data = {k: v for k, v in asdict(obj).items() if k not in VOLATILE}
    return Item(kind, obj.id, data, pw)


def item_hash(item: Item, hkey: bytes) -> str:
    canonical = json.dumps({"k": item.kind, "d": item.data, "p": item.pw}, sort_keys=True, ensure_ascii=False)
    return hmac.new(hkey or b"-", canonical.encode("utf-8"), hashlib.sha256).hexdigest()


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
    new_meta: bool = False
    group_colors: dict = field(default_factory=dict)


@dataclass
class SyncReport:
    added: int = 0
    updated: int = 0
    removed: int = 0
    conflicts: list[str] = field(default_factory=list)
    local_changed: bool = False
    committed: bool = False
    pushed: bool = False

    def summary(self) -> str:
        parts = []
        if self.added or self.updated or self.removed:
            parts.append(tr("lokal: {added} neu, {updated} geändert, {removed} entfernt", added=self.added, updated=self.updated, removed=self.removed))
        if self.pushed:
            parts.append(tr("Änderungen hochgeladen"))
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

    def _save_state(self, state: dict) -> None:
        self.work.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        os.replace(tmp, self.state_path)

    # ---- Phase 1: Server-Stand holen ----
    def fetch(self) -> RemoteState:
        if not self.s.url.strip():
            raise SyncError(tr("Keine Repository-URL eingestellt."))
        branch = self.s.branch.strip() or "main"
        self.repo.parent.mkdir(parents=True, exist_ok=True)
        if not (self.repo / ".git").exists():
            self.repo.mkdir(parents=True, exist_ok=True)
            Git(self.repo).run("init", "-q")
            self.git.run("remote", "add", "origin", self.s.url)
        else:
            self.git.run("remote", "set-url", "origin", self.s.url)
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
        self._remote = self._read_remote(exists)
        return self._remote

    def _read_remote(self, branch_exists: bool) -> RemoteState:
        meta = None
        meta_path = self.repo / "meta.json"
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except ValueError as exc:
                raise SyncError(tr("meta.json im Repository ist beschädigt.")) from exc
        key, new_meta = None, False
        if self.s.passwords:
            if not self.passphrase:
                raise SyncError(tr("Das Sync-Passwort fehlt (Seitenleiste → Synchronisation …)."))
            if meta and meta.get("kdf"):
                key = derive_key(self.passphrase, meta["kdf"])
                decrypt(key, meta["check"], "check")  # prüft das Sync-Passwort
            else:
                meta = {"format": FORMAT, "kdf": new_kdf()}
                key = derive_key(self.passphrase, meta["kdf"])
                meta["check"] = encrypt(key, "ssh-verwaltung", "check")  # Prüftext, bewusst unverändert (Kompatibilität)
                new_meta = True
        items: dict = {}
        for kind, (folder, _cls) in KINDS.items():
            for path in sorted((self.repo / folder).glob("*.json")) if (self.repo / folder).is_dir() else []:
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    blob = data.pop("password_enc", None)
                    item_id = data["id"]
                    # auf bekannte Felder normalisieren (fehlende Felder bekommen Standardwerte)
                    data = {k: v for k, v in asdict(_cls.from_dict(data)).items() if k not in VOLATILE}
                except (ValueError, KeyError, OSError):
                    continue  # beschädigte Datei überspringen
                pw = decrypt(key, blob, item_id) if (key and blob) else None
                items[(kind, item_id)] = Item(kind, item_id, data, pw, blob)
        groups: set = set()
        colors: dict = {}
        gp = self.repo / "groups.json"
        if gp.exists():
            try:
                gdata = json.loads(gp.read_text(encoding="utf-8"))
                groups = set(gdata.get("groups", []))
                raw = gdata.get("colors")
                if isinstance(raw, dict):
                    colors = {k: v for k, v in raw.items() if isinstance(k, str) and isinstance(v, str) and v}
            except ValueError:
                pass
        return RemoteState(items, groups, meta, key, branch_exists, new_meta, colors)

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

    def merge(self, cfg: Config, remote: RemoteState | None = None) -> SyncReport:
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
        for k, it in local.items():
            # Passwort fehlt lokal im Schlüsselbund: nicht als „gelöscht“ auf den Server übertragen, sondern wiederherstellen
            if it.pw_missing and k in remote.items and remote.items[k].pw is not None:
                it.pw = remote.items[k].pw
        result, conflicts = merge_items(base_key, local, remote.items, lambda it: item_hash(it, hkey))
        report = SyncReport()
        for k, loc, rem in conflicts:
            copy_id = new_id()
            data = dict(rem.data, id=copy_id)
            data["name"] = tr("{v} (Konflikt)", v=data.get('name', ''))
            result[(k[0], copy_id)] = Item(k[0], copy_id, data, rem.pw)
            report.conflicts.append(loc.data.get("name", loc.id))

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
        }
        return report

    def _apply_local(self, cfg: Config, local: dict, result: dict, pw_on: bool, report: SyncReport) -> None:
        old = {k: o for kind, objs in (("c", cfg.connections), ("i", cfg.identities), ("k", cfg.ssh_keys), ("s", cfg.snippets))
               for o in objs for k in [(kind, o.id)]}
        new_conns: list[Connection] = []
        new_idents: list[Identity] = []
        new_keys: list[SshKey] = []
        new_snippets: list[Snippet] = []
        for k, item in result.items():
            _folder, cls = KINDS[k[0]]
            obj = cls.from_dict(dict(item.data, id=item.id))
            existing = old.get(k)
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
                obj.use_password = bool(existing.use_password) if existing else False
            if existing is None:
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
                report.removed += 1
                if pw_on and o.use_password:
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
            branch = self.s.branch.strip() or "main"
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

    def run(self, cfg: Config, retries: int = 3) -> SyncReport:
        """Kompletter Abgleich in einem Aufruf (blockierend)."""
        for attempt in range(retries):
            remote = self.fetch()
            report = self.merge(cfg, remote)
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
