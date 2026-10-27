import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sesam import syncing
from sesam.models import Config, Connection, Identity, SyncSettings
from sesam.syncing import SyncEngine, SyncError, SyncRetry, merge_items, merge_sets, Item


class DictStore:
    def __init__(self):
        self.d = {}

    def get(self, k):
        return self.d.get(k)

    def set(self, k, v):
        self.d[k] = v

    def delete(self, k):
        self.d.pop(k, None)


def git(*args, cwd=None):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


class MergeLogicTests(unittest.TestCase):
    h = staticmethod(lambda it: json.dumps(it.data, sort_keys=True) + str(it.pw))

    def item(self, name, pw=None, id="1"):
        return Item("c", id, {"id": id, "name": name}, pw)

    def test_three_way_cases(self):
        h = self.h
        a, b = self.item("a"), self.item("b")
        k = ("c", "1")
        base = {k: h(a)}
        self.assertEqual(merge_items(base, {k: b}, {k: a}, h)[0][k], b)      # nur lokal geändert
        self.assertEqual(merge_items(base, {k: a}, {k: b}, h)[0][k], b)      # nur Server geändert
        self.assertEqual(merge_items(base, {k: a}, {}, h)[0], {})            # am Server gelöscht
        self.assertEqual(merge_items(base, {}, {k: a}, h)[0], {})            # lokal gelöscht
        self.assertEqual(merge_items(base, {k: b}, {}, h)[0][k], b)          # geändert + am Server gelöscht: bleibt
        self.assertEqual(merge_items(base, {}, {k: b}, h)[0][k], b)
        self.assertEqual(merge_items({}, {k: a}, {}, h)[0][k], a)            # neu lokal
        self.assertEqual(merge_items({}, {}, {k: a}, h)[0][k], a)            # neu am Server
        res, conflicts = merge_items(base, {k: self.item("x")}, {k: self.item("y")}, h)
        self.assertEqual(res[k].data["name"], "x")
        self.assertEqual(len(conflicts), 1)

    def test_merge_sets(self):
        self.assertEqual(merge_sets({"a", "b"}, {"a", "c"}, {"b", "d"}), {"c", "d"})


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.remote = root / "remote.git"
        git("init", "-q", "--bare", "-b", "main", str(self.remote))
        self.devices = {}

    def device(self, name, passwords_on=True, passphrase="geheim"):
        root = Path(self.tmp.name) / name
        settings = SyncSettings(enabled=True, url=str(self.remote), branch="main", passwords=passwords_on)
        store = DictStore()
        engine = SyncEngine(settings, root / "sync", passphrase, store, device=name)
        cfg = Config()
        self.devices[name] = (cfg, engine, store)
        return cfg, engine, store

    def add_conn(self, cfg, store, name, pw=None, **kw):
        c = Connection(name, kw.pop("host", name + ".example"), **kw)
        if pw:
            store.set(c.id, pw)
            c.use_password = True
        cfg.connections.append(c)
        return c

    def remote_text(self):
        out = git("--git-dir", str(self.remote), "ls-tree", "-r", "--name-only", "main")
        texts = {}
        for f in out.split():
            texts[f] = git("--git-dir", str(self.remote), "show", f"main:{f}")
        return texts

    def test_first_device_pushes_encrypted_and_second_device_receives(self):
        cfgA, A, storeA = self.device("A")
        ident = Identity("deploy", user="deploy", identity_file="/k", use_password=True)
        storeA.set(ident.id, "id-pw")
        cfgA.identities.append(ident)
        conn = self.add_conn(cfgA, storeA, "web", pw="conn-pw", identity_id="", group="Kunde/Prod")
        c2 = self.add_conn(cfgA, storeA, "db", identity_id=ident.id)
        cfgA.groups = ["Leer"]
        rep = A.run(cfgA)
        self.assertTrue(rep.pushed)

        files = self.remote_text()
        self.assertIn("meta.json", files)
        self.assertIn(f"connections/{conn.id}.json", files)
        blob = "\n".join(files.values())
        self.assertNotIn("conn-pw", blob)  # kein Klartext im Repository
        self.assertNotIn("id-pw", blob)
        self.assertIn("password_enc", files[f"connections/{conn.id}.json"])
        self.assertNotIn("password_enc", files[f"connections/{c2.id}.json"])

        cfgB, B, storeB = self.device("B")
        repB = B.run(cfgB)
        self.assertEqual((repB.added, repB.removed), (3, 0))
        self.assertEqual({c.name for c in cfgB.connections}, {"web", "db"})
        got = {c.name: c for c in cfgB.connections}
        self.assertTrue(got["web"].use_password and storeB.get(got["web"].id) == "conn-pw")
        self.assertEqual(got["db"].identity_id, ident.id)
        self.assertEqual(storeB.get(ident.id), "id-pw")
        self.assertEqual(cfgB.groups, ["Leer"])
        self.assertFalse(repB.pushed)  # nichts zu senden

    def test_changes_merge_across_devices_and_deletions_propagate(self):
        cfgA, A, sA = self.device("A")
        a1 = self.add_conn(cfgA, sA, "eins", pw="p1")
        a2 = self.add_conn(cfgA, sA, "zwei")
        a3 = self.add_conn(cfgA, sA, "drei", pw="p3")
        A.run(cfgA)
        cfgB, B, sB = self.device("B")
        B.run(cfgB)

        # A ändert "eins" (Host), B ändert "zwei" (Port) und löscht "drei"
        a1.host = "neu.example"
        A.run(cfgA)
        b2 = next(c for c in cfgB.connections if c.name == "zwei")
        b2.port = 2222
        b3 = next(c for c in cfgB.connections if c.name == "drei")
        cfgB.connections.remove(b3)
        sB.delete(b3.id)
        repB = B.run(cfgB)
        self.assertTrue(repB.pushed)
        self.assertEqual(next(c for c in cfgB.connections if c.name == "eins").host, "neu.example")

        repA = A.run(cfgA)
        self.assertEqual(repA.removed, 1)
        self.assertEqual({c.name for c in cfgA.connections}, {"eins", "zwei"})
        self.assertEqual(next(c for c in cfgA.connections if c.name == "zwei").port, 2222)
        self.assertIsNone(sA.get(a3.id))  # Passwort der gelöschten Verbindung entfernt

    def test_password_change_propagates_and_missing_local_password_is_restored(self):
        cfgA, A, sA = self.device("A")
        c = self.add_conn(cfgA, sA, "x", pw="alt")
        A.run(cfgA)
        cfgB, B, sB = self.device("B")
        B.run(cfgB)
        sA.set(c.id, "neu")
        A.run(cfgA)
        B.run(cfgB)
        self.assertEqual(sB.get(c.id), "neu")
        # Schlüsselbund auf B geleert: darf das Passwort nicht serverseitig löschen
        sB.d.clear()
        repB = B.run(cfgB)
        self.assertFalse(repB.pushed)
        self.assertEqual(sB.get(c.id), "neu")

    def conflict_setup(self):
        cfgA, A, sA = self.device("A")
        a = self.add_conn(cfgA, sA, "gemeinsam")
        A.run(cfgA)
        cfgB, B, sB = self.device("B")
        B.run(cfgB)
        a.host = "von-a"
        A.run(cfgA)
        next(c for c in cfgB.connections).host = "von-b"
        return cfgA, A, cfgB, B

    def test_conflict_without_resolver_keeps_local_and_makes_no_copy(self):
        cfgA, A, cfgB, B = self.conflict_setup()
        rep = B.run(cfgB)
        self.assertEqual(rep.conflicts, ["gemeinsam"])
        self.assertEqual({c.name: c.host for c in cfgB.connections}, {"gemeinsam": "von-b"})
        A.run(cfgA)  # lokale Version von B wurde hochgeladen
        self.assertEqual({c.name: c.host for c in cfgA.connections}, {"gemeinsam": "von-b"})

    def test_conflict_choose_local_uploads_local_version(self):
        cfgA, A, cfgB, B = self.conflict_setup()
        asked = []

        def resolve(conflicts):
            asked.append([(c.name, c.kind, c.differences()) for c in conflicts])
            return {c.token: "local" for c in conflicts}

        rep = B.run(cfgB, resolve=resolve)
        self.assertEqual(asked, [[("gemeinsam", "c", [("host", "von-b", "von-a")])]])
        self.assertTrue(rep.pushed)
        self.assertEqual(rep.kept_remote, [])
        self.assertEqual({c.name: c.host for c in cfgB.connections}, {"gemeinsam": "von-b"})
        A.run(cfgA)
        self.assertEqual({c.name: c.host for c in cfgA.connections}, {"gemeinsam": "von-b"})

    def test_conflict_choose_remote_replaces_local_version(self):
        cfgA, A, cfgB, B = self.conflict_setup()
        rep = B.run(cfgB, resolve=lambda cs: {c.token: "remote" for c in cs})
        self.assertEqual(rep.kept_remote, ["gemeinsam"])
        self.assertEqual({c.name: c.host for c in cfgB.connections}, {"gemeinsam": "von-a"})
        rep = A.run(cfgA)
        self.assertFalse(rep.pushed)
        self.assertEqual({c.name: c.host for c in cfgA.connections}, {"gemeinsam": "von-a"})
        self.assertFalse(B.run(cfgB).conflicts)  # danach kein Konflikt mehr

    def test_conflict_cancel_changes_nothing(self):
        cfgA, A, cfgB, B = self.conflict_setup()
        with self.assertRaises(SyncError):
            B.run(cfgB, resolve=lambda cs: None)
        self.assertEqual({c.name: c.host for c in cfgB.connections}, {"gemeinsam": "von-b"})
        rep = B.run(cfgB, resolve=lambda cs: {c.token: "local" for c in cs})  # Konflikt besteht weiter
        self.assertTrue(rep.pushed)

    def test_wrong_passphrase_and_missing_passphrase(self):
        cfgA, A, sA = self.device("A")
        self.add_conn(cfgA, sA, "x", pw="p")
        A.run(cfgA)
        cfgB, B, _ = self.device("B", passphrase="falsch")
        with self.assertRaises(SyncError) as cm:
            B.run(cfgB)
        self.assertIn("Sync-Passwort", str(cm.exception))
        cfgC, C, _ = self.device("C", passphrase=None)
        with self.assertRaises(SyncError):
            C.run(cfgC)

    def test_without_password_sync_blobs_are_preserved(self):
        cfgA, A, sA = self.device("A")
        c = self.add_conn(cfgA, sA, "x", pw="p")
        A.run(cfgA)
        cfgB, B, sB = self.device("B", passwords_on=False, passphrase=None)
        B.run(cfgB)
        got = cfgB.connections[0]
        self.assertFalse(got.use_password)           # Passwort kam nicht mit
        self.assertIsNone(sB.get(got.id))
        got.host = "geaendert"
        B.run(cfgB)
        self.assertIn("password_enc", self.remote_text()[f"connections/{c.id}.json"])  # Block bleibt erhalten
        A.run(cfgA)
        self.assertEqual(cfgA.connections[0].host, "geaendert")
        self.assertEqual(sA.get(c.id), "p")

    def test_failed_push_does_not_lose_new_items(self):
        cfgA, A, sA = self.device("A")
        self.add_conn(cfgA, sA, "neu")
        remote = A.fetch()
        A.merge(cfgA, remote)
        with mock.patch.object(A.git, "run", wraps=A.git.run) as run:
            def failing(*args, **kw):
                if args and args[0] == "push":
                    raise SyncError("git push fehlgeschlagen: Netzwerk")
                return git_run(*args, **kw)
            git_run = syncing.Git.run.__get__(A.git)
            run.side_effect = failing
            with self.assertRaises(SyncError):
                A.push()
        self.assertFalse(A.state_path.exists())      # Stand wird nur nach erfolgreichem Upload gespeichert
        A.run(cfgA)                                   # nächster Versuch: Eintrag bleibt, wird hochgeladen
        self.assertEqual([c.name for c in cfgA.connections], ["neu"])
        self.assertIn("neu", "".join(self.remote_text().values()))

    def test_push_race_is_retried(self):
        cfgA, A, sA = self.device("A")
        self.add_conn(cfgA, sA, "a-conn")
        A.run(cfgA)
        cfgB, B, sB = self.device("B")
        B.run(cfgB)
        self.add_conn(cfgA, sA, "a2")
        remote = A.fetch()
        rep = A.merge(cfgA, remote)
        self.add_conn(cfgB, sB, "b-neu")
        B.run(cfgB)                                   # B kommt A zuvor
        with self.assertRaises(SyncRetry):
            A.push()
        A.run(cfgA)                                   # kompletter Neuversuch
        self.assertEqual({c.name for c in cfgA.connections}, {"a-conn", "a2", "b-neu"})
        B.run(cfgB)
        self.assertEqual({c.name for c in cfgB.connections}, {"a-conn", "a2", "b-neu"})

    def test_deleted_identity_clears_references(self):
        cfgA, A, sA = self.device("A")
        ident = Identity("i")
        cfgA.identities.append(ident)
        self.add_conn(cfgA, sA, "c", identity_id=ident.id)
        A.run(cfgA)
        cfgB, B, _ = self.device("B")
        B.run(cfgB)
        cfgA.identities.clear()
        A.run(cfgA)
        B.run(cfgB)
        self.assertEqual(cfgB.identities, [])
        self.assertEqual(cfgB.connections[0].identity_id, "")

    def test_group_changes_merge(self):
        cfgA, A, sA = self.device("A")
        cfgA.groups = ["alt", "bleibt"]
        A.run(cfgA)
        cfgB, B, _ = self.device("B")
        B.run(cfgB)
        cfgA.groups = ["bleibt", "von-a"]
        A.run(cfgA)
        cfgB.groups = ["alt", "bleibt", "von-b"]
        B.run(cfgB)
        self.assertEqual(sorted(cfgB.groups), ["bleibt", "von-a", "von-b"])

    def test_group_colors_connection_colors_and_tags_sync(self):
        cfgA, A, sA = self.device("A")
        self.add_conn(cfgA, sA, "web", group="Kunde", color="red", tags=["prod", "web"])
        cfgA.group_colors = {"Kunde": "blue"}
        A.run(cfgA)
        self.assertEqual(json.loads(self.remote_text()["groups.json"])["colors"], {"Kunde": "blue"})
        cfgB, B, _ = self.device("B")
        B.run(cfgB)
        self.assertEqual(cfgB.group_colors, {"Kunde": "blue"})
        self.assertEqual((cfgB.connections[0].color, cfgB.connections[0].tags), ("red", ["prod", "web"]))
        # Änderung auf B kommt auf A an, Entfernen ebenfalls
        cfgB.group_colors = {"Kunde": "green", "Neu": "pink"}
        B.run(cfgB)
        A.run(cfgA)
        self.assertEqual(cfgA.group_colors, {"Kunde": "green", "Neu": "pink"})
        cfgA.group_colors = {"Neu": "pink"}
        A.run(cfgA)
        B.run(cfgB)
        self.assertEqual(cfgB.group_colors, {"Neu": "pink"})

    def test_groups_file_keeps_old_format_without_colors(self):
        cfgA, A, sA = self.device("A")
        cfgA.groups = ["G"]
        A.run(cfgA)
        self.assertEqual(json.loads(self.remote_text()["groups.json"]), {"groups": ["G"]})

    def test_missing_url_and_unreachable_remote(self):
        cfg = Config()
        eng = SyncEngine(SyncSettings(url=""), Path(self.tmp.name) / "x", None, DictStore())
        with self.assertRaises(SyncError):
            eng.run(cfg)
        eng = SyncEngine(SyncSettings(url=str(Path(self.tmp.name) / "gibt-es-nicht.git"), passwords=False),
                         Path(self.tmp.name) / "y", None, DictStore())
        with self.assertRaises(SyncError):
            eng.run(cfg)


class CredentialTests(unittest.TestCase):
    def fill(self, creds):
        """Fragt git (wie bei einem HTTPS-Zugriff) über den Credential-Helper nach den Zugangsdaten."""
        import os
        args, env = syncing.credential_config(creds)
        proc = subprocess.run(
            ["git", *args, "credential", "fill"], input="protocol=https\nhost=example.com\n\n",
            capture_output=True, text=True, encoding="utf-8", env={**os.environ, **env, "GIT_TERMINAL_PROMPT": "0"},
        )
        return proc.returncode, dict(line.split("=", 1) for line in proc.stdout.splitlines() if "=" in line)

    def test_helper_returns_user_and_password_even_with_special_characters(self):
        for pw in ("geheim", 'p w$"\'`!\\n%s&;ß', ""):
            code, out = self.fill(("benutzer@mail.de", pw))
            self.assertEqual(code, 0)
            self.assertEqual(out["username"], "benutzer@mail.de")
            self.assertEqual(out["password"], pw)

    def test_no_credentials_means_no_extra_options(self):
        self.assertEqual(syncing.credential_config(None), ([], {}))

    def test_credentials_never_appear_in_the_command_line(self):
        calls = []

        def fake(cmd, **kw):
            calls.append((cmd, kw["env"]))
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with mock.patch("sesam.syncing.subprocess.run", fake):
            syncing.Git(Path("."), ("anna", "topsecret")).run("ls-remote", "origin")
            syncing.Git(Path(".")).run("ls-remote", "origin")
        (cmd, env), (cmd2, env2) = calls
        self.assertNotIn("topsecret", " ".join(cmd))
        self.assertNotIn("anna", " ".join(cmd))
        self.assertEqual((env["SESAM_GIT_USER"], env["SESAM_GIT_PASSWORD"]), ("anna", "topsecret"))
        self.assertNotIn("credential.helper", " ".join(cmd2))
        self.assertNotIn("SESAM_GIT_PASSWORD", env2)

    def test_load_credentials(self):
        store = {}
        with mock.patch("sesam.syncing.passwords.get_password", lambda k: store.get(k)):
            self.assertIsNone(syncing.load_credentials(SyncSettings(username="")))
            self.assertEqual(syncing.load_credentials(SyncSettings(username=" anna ")), ("anna", ""))
            store[syncing.GIT_PASSWORD_KEY] = "tok"
            self.assertEqual(syncing.load_credentials(SyncSettings(username="anna", git_password_saved=True)), ("anna", "tok"))


if __name__ == "__main__":
    unittest.main()
