"""Gleiche Einträge mit verschiedenen IDs (z. B. Import aus ~/.ssh/config auf beiden Geräten): Abgleich und Bereinigung."""
import unittest

import test_sync

from sesam import duplicates
from sesam.models import Config, Connection, Identity


class SyncDuplicateTests(unittest.TestCase):
    def setUp(self):
        self.h = test_sync.EngineTests("test_first_device_pushes_encrypted_and_second_device_receives")  # nur als Hilfe (Server, Geräte)
        self.h.setUp()
        self.addCleanup(self.h.tmp.cleanup)
        self.device, self.add_conn, self.remote_text = self.h.device, self.h.add_conn, self.h.remote_text

    def names(self, cfg):
        return sorted((c.name, c.host) for c in cfg.connections)

    def test_same_connection_created_on_both_devices_is_not_duplicated(self):
        cfgA, A, sA = self.device("A")
        a = self.add_conn(cfgA, sA, "web", host="web.example", user="root", port=2222, group="Prod")
        A.run(cfgA)
        cfgB, B, sB = self.device("B")
        b = self.add_conn(cfgB, sB, "web", host="web.example", user="root", port=2222, group="Prod")
        self.assertNotEqual(a.id, b.id)
        rep = B.run(cfgB)
        self.assertEqual(rep.merged, 1)
        self.assertEqual((rep.added, rep.removed, rep.conflicts), (0, 0, []))
        self.assertEqual([c.id for c in cfgB.connections], [a.id])  # Server-ID gilt
        self.assertFalse(rep.pushed)
        A.run(cfgA)
        self.assertEqual(len(cfgA.connections), 1)
        self.assertEqual([f for f in self.remote_text() if f.startswith("connections/")], [f"connections/{a.id}.json"])

    def test_nothing_is_merged_after_both_devices_know_the_entries(self):
        cfgA, A, sA = self.device("A")
        self.add_conn(cfgA, sA, "web", host="h")
        A.run(cfgA)
        cfgB, B, sB = self.device("B")
        B.run(cfgB)
        self.add_conn(cfgB, sB, "web", host="h")  # später bewusst ein zweiter, gleicher Eintrag
        rep = B.run(cfgB)
        self.assertEqual(rep.merged, 0)
        self.assertEqual(len(cfgB.connections), 2)

    def test_different_connections_stay_separate(self):
        cfgA, A, sA = self.device("A")
        self.add_conn(cfgA, sA, "web", host="h1")
        A.run(cfgA)
        cfgB, B, sB = self.device("B")
        self.add_conn(cfgB, sB, "web", host="h2")  # gleicher Name, anderer Host
        rep = B.run(cfgB)
        self.assertEqual((rep.merged, rep.added), (0, 1))
        self.assertEqual(self.names(cfgB), [("web", "h1"), ("web", "h2")])

    def test_password_moves_to_the_server_id(self):
        cfgA, A, sA = self.device("A")
        a = self.add_conn(cfgA, sA, "db", host="db", pw="geheim")
        A.run(cfgA)
        cfgB, B, sB = self.device("B")
        b = self.add_conn(cfgB, sB, "db", host="db", pw="geheim")
        B.run(cfgB)
        self.assertEqual(sB.get(a.id), "geheim")
        self.assertIsNone(sB.get(b.id))
        self.assertTrue(cfgB.connections[0].use_password)

    def test_password_moves_without_password_sync_too(self):
        cfgA, A, sA = self.device("A", passwords_on=False)
        a = self.add_conn(cfgA, sA, "db", host="db")
        A.run(cfgA)
        cfgB, B, sB = self.device("B", passwords_on=False)
        b = self.add_conn(cfgB, sB, "db", host="db", pw="lokal")
        rep = B.run(cfgB)
        self.assertEqual(rep.merged, 1)
        self.assertEqual(sB.get(a.id), "lokal")
        self.assertIsNone(sB.get(b.id))
        self.assertTrue(cfgB.connections[0].use_password)

    def test_difference_in_details_asks_which_version_counts(self):
        cfgA, A, sA = self.device("A")
        self.add_conn(cfgA, sA, "web", host="h", extra_args="-A")
        A.run(cfgA)
        cfgB, B, sB = self.device("B")
        self.add_conn(cfgB, sB, "web", host="h", extra_args="-X")
        asked = []

        def resolve(conflicts):
            asked.extend(c.differences() for c in conflicts)
            return {c.token: "remote" for c in conflicts}

        rep = B.run(cfgB, resolve=resolve)
        self.assertEqual(asked, [[("extra_args", "-X", "-A")]])
        self.assertEqual((rep.merged, len(cfgB.connections), cfgB.connections[0].extra_args), (1, 1, "-A"))

    def test_identity_is_merged_and_connections_follow(self):
        cfgA, A, sA = self.device("A")
        ia = Identity("deploy", user="deploy")
        cfgA.identities.append(ia)
        A.run(cfgA)
        cfgB, B, sB = self.device("B")
        ib = Identity("deploy", user="deploy")
        cfgB.identities.append(ib)
        self.add_conn(cfgB, sB, "app", host="app", identity_id=ib.id)
        rep = B.run(cfgB)
        self.assertEqual([i.id for i in cfgB.identities], [ia.id])
        self.assertEqual(cfgB.connections[0].identity_id, ia.id)
        self.assertEqual((rep.merged, rep.conflicts), (1, []))
        A.run(cfgA)
        self.assertEqual(cfgA.connections[0].identity_id, ia.id)  # Verweis ist auch auf dem Server gültig

    def test_summary_mentions_merged_entries(self):
        cfgA, A, sA = self.device("A")
        self.add_conn(cfgA, sA, "web", host="h")
        A.run(cfgA)
        cfgB, B, sB = self.device("B")
        self.add_conn(cfgB, sB, "web", host="h")
        self.assertIn("1 doppelte", B.run(cfgB).summary())


class CleanupTests(unittest.TestCase):
    def cfg(self):
        a = Connection("web", "h", user="u", id="a")
        b = Connection("web", "H", user="u", id="b", use_password=True)
        c = Connection("web", "h", user="u", id="c", use_password=True)
        other = Connection("db", "d", id="d")
        diff_group = Connection("web", "h", user="u", id="e", group="x")
        return Config(connections=[a, b, c, other, diff_group])

    def test_finds_groups_with_the_keeper_first(self):
        groups = duplicates.find_duplicate_connections(self.cfg())
        self.assertEqual([[c.id for c in g] for g in groups], [["b", "c", "a"]])  # mit Passwort zuerst, dann kleinste ID

    def test_no_duplicates(self):
        self.assertEqual(duplicates.find_duplicate_connections(Config(connections=[Connection("a", "h"), Connection("b", "h")])), [])

    def test_remove_keeps_first_and_deletes_passwords_of_removed(self):
        cfg = self.cfg()
        deleted = []
        removed, failures = duplicates.remove_duplicates(cfg, duplicates.find_duplicate_connections(cfg), deleted.append)
        self.assertEqual(sorted(c.id for c in removed), ["a", "c"])
        self.assertEqual(deleted, ["c"])  # nur der entfernte Eintrag mit Passwort
        self.assertEqual(sorted(c.id for c in cfg.connections), ["b", "d", "e"])
        self.assertEqual(failures, [])

    def test_password_errors_are_reported_but_removal_continues(self):
        cfg = self.cfg()

        def boom(_id):
            raise RuntimeError("Schlüsselbund gesperrt")

        removed, failures = duplicates.remove_duplicates(cfg, duplicates.find_duplicate_connections(cfg), boom)
        self.assertEqual(len(removed), 2)
        self.assertIn("Schlüsselbund gesperrt", failures[0])


if __name__ == "__main__":
    unittest.main()
