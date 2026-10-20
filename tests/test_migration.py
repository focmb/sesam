import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sesam import migration, storage
from sesam.models import Config, Connection, Identity


class DictStore:
    def __init__(self, d=None):
        self.d = dict(d or {})

    def get(self, k):
        return self.d.get(k)

    def set(self, k, v):
        self.d[k] = v

    def delete(self, k):
        self.d.pop(k, None)


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": self.tmp.name, "APPDATA": self.tmp.name})
        env.start()
        self.addCleanup(env.stop)
        self.base = Path(self.tmp.name)

    def _legacy_config(self):
        legacy = self.base / "ssh_verwaltung"
        legacy.mkdir()
        c = Connection("alt", "h", use_password=True)
        i = Identity("ident", use_password=True)
        cfg = Config(connections=[c], identities=[i])
        storage.save(cfg, legacy / "config.json")
        (legacy / "extra.txt").write_text("bleibt")
        return c, i

    def test_config_dir_is_moved_once(self):
        self._legacy_config()
        self.assertTrue(migration.migrate_config_dir())
        self.assertFalse((self.base / "ssh_verwaltung").exists())
        self.assertEqual(len(storage.load().connections), 1)
        self.assertEqual((self.base / "sesam" / "extra.txt").read_text(), "bleibt")
        self.assertFalse(migration.migrate_config_dir())

    def test_existing_new_dir_is_not_overwritten(self):
        self._legacy_config()
        (self.base / "sesam").mkdir()
        self.assertFalse(migration.migrate_config_dir())
        self.assertTrue((self.base / "ssh_verwaltung").exists())

    def test_passwords_are_copied_then_removed_from_old_service(self):
        c, i = self._legacy_config()
        old = DictStore({c.id: "pw-c", i.id: "pw-i", "sync:passphrase": "phrase", "fremd": "bleibt"})
        new = DictStore()
        migration.run(old, new)
        self.assertEqual(new.d, {c.id: "pw-c", i.id: "pw-i", "sync:passphrase": "phrase"})
        self.assertEqual(old.d, {"fremd": "bleibt"})
        self.assertTrue((self.base / "sesam" / migration.MARKER).exists())

    def test_runs_only_once_and_tolerates_keyring_errors(self):
        c, i = self._legacy_config()
        boom = mock.Mock()
        boom.get.side_effect = RuntimeError("Schlüsselbund gesperrt")
        logs = []
        migration.run(boom, DictStore(), log=logs.append)           # kein Absturz, kein Marker
        self.assertFalse((self.base / "sesam" / migration.MARKER).exists())
        self.assertTrue(any("erneut" in m for m in logs))
        old = DictStore({c.id: "pw"})
        migration.run(old, DictStore())                              # zweiter Versuch gelingt
        self.assertTrue((self.base / "sesam" / migration.MARKER).exists())
        again = DictStore({c.id: "neu-alt"})
        migration.run(again, DictStore())                            # danach nichts mehr
        self.assertEqual(again.d, {c.id: "neu-alt"})

    def test_does_not_overwrite_existing_new_password(self):
        c, _ = self._legacy_config()
        old, new = DictStore({c.id: "alt"}), DictStore({c.id: "neu"})
        migration.run(old, new)
        self.assertEqual(new.d[c.id], "neu")
        self.assertEqual(old.d, {})

    def test_fresh_install_without_legacy(self):
        migration.run(DictStore(), DictStore())
        self.assertTrue((self.base / "sesam" / migration.MARKER).exists())


if __name__ == "__main__":
    unittest.main()
