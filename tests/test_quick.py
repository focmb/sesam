"""Favoriten (synchronisiert) und Schnellstarter (Suche, „Zuletzt verwendet“ nur dort)."""
import json
import tempfile
import time
import unittest
from pathlib import Path

from gui_base import GuiTestCase

from sesam import quick, storage, syncing
from sesam.models import Config, Connection


def _cfg():
    return Config(connections=[
        Connection("web-prod", "10.0.0.1", user="deploy", group="Firma", tags=["nginx"]),
        Connection("db-prod", "10.0.0.2", user="postgres", group="Firma"),
        Connection("nas", "nas.lan", user="admin"),
        Connection("pi", "192.168.1.5", user="pi"),
    ])


class SearchTests(unittest.TestCase):
    def names(self, cfg, query=""):
        return [(h.conn.name, h.section) for h in quick.search(cfg, query)]

    def test_sections_and_each_connection_once(self):
        cfg = _cfg()
        web, db, nas, pi = cfg.connections
        web.favorite = True
        cfg.recent = {web.id: 300.0, db.id: 200.0, nas.id: 100.0}
        self.assertEqual(self.names(cfg), [
            ("web-prod", quick.FAVORITES), ("db-prod", quick.RECENT), ("nas", quick.RECENT), ("pi", quick.ALL)])

    def test_recent_without_query_is_limited_and_sorted_by_time(self):
        cfg = Config(connections=[Connection(f"c{i}", "h") for i in range(12)])
        cfg.recent = {c.id: float(i + 1) for i, c in enumerate(cfg.connections)}
        hits = quick.search(cfg)
        recent = [h.conn.name for h in hits if h.section == quick.RECENT]
        self.assertEqual(len(recent), quick.RECENT_SHOWN)
        self.assertEqual(recent[0], "c11")
        self.assertEqual(len(hits), 12)
        # mit Suchbegriff bleiben alle passenden zuvor verwendeten im Abschnitt
        self.assertEqual(len([h for h in quick.search(cfg, "c") if h.section == quick.RECENT]), 12)

    def test_query_matches_all_fields_with_and(self):
        cfg = _cfg()
        self.assertEqual(sorted(n for n, _ in self.names(cfg, "prod")), ["db-prod", "web-prod"])
        self.assertEqual(sorted(n for n, _ in self.names(cfg, "firma prod")), ["db-prod", "web-prod"])
        self.assertEqual([n for n, _ in self.names(cfg, "postgres")], ["db-prod"])
        self.assertEqual([n for n, _ in self.names(cfg, "nginx")], ["web-prod"])
        self.assertEqual([n for n, _ in self.names(cfg, "prod nas")], [])

    def test_fuzzy_and_ranking(self):
        cfg = _cfg()
        self.assertIn("web-prod", [n for n, _ in self.names(cfg, "wbpd")])
        cfg.connections.append(Connection("prod-x", "h"))
        self.assertEqual(self.names(cfg, "prod")[0][0], "prod-x")  # Treffer am Wortanfang vor dem Mittendrin

    def test_positions_highlight_the_name_only(self):
        hit = [h for h in quick.search(_cfg(), "prod") if h.conn.name == "web-prod"][0]
        self.assertEqual(hit.positions, [4, 5, 6, 7])
        hit = [h for h in quick.search(_cfg(), "postgres")][0]
        self.assertEqual(hit.positions, [])  # Treffer im Benutzer, nicht im Namen

    def test_ago(self):
        self.assertEqual(quick.ago(5), "gerade eben")
        self.assertEqual(quick.ago(300), "vor 5 Min.")
        self.assertEqual(quick.ago(7200), "vor 2 Std.")
        self.assertEqual(quick.ago(90000), "gestern")
        self.assertEqual(quick.ago(3 * 86400), "vor 3 Tagen")


class StorageTests(unittest.TestCase):
    def test_favorite_and_recent_roundtrip_and_bad_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "c.json"
            cfg = _cfg()
            cfg.connections[0].favorite = True
            cfg.recent = {cfg.connections[1].id: 123.5}
            storage.save(cfg, p)
            back = storage.load(p)
            self.assertTrue(back.connections[0].favorite)
            self.assertEqual(back.recent, cfg.recent)
            data = json.loads(p.read_text(encoding="utf-8"))
            data["recent"] = {"a": "x", "b": float("inf"), "c": 5, "d": None}
            p.write_text(json.dumps(data), encoding="utf-8")
            self.assertEqual(storage.load(p).recent, {"c": 5.0})
            data["recent"] = ["kaputt"]
            p.write_text(json.dumps(data), encoding="utf-8")
            self.assertEqual(storage.load(p).recent, {})
            del data["recent"]
            p.write_text(json.dumps(data), encoding="utf-8")
            self.assertEqual(storage.load(p).recent, {})

    def test_recent_is_pruned(self):
        raw = {f"id{i}": float(i) for i in range(storage.RECENT_MAX + 20)}
        out = storage._recent(raw)
        self.assertEqual(len(out), storage.RECENT_MAX)
        self.assertIn(f"id{storage.RECENT_MAX + 19}", out)
        self.assertNotIn("id0", out)


class SyncTests(unittest.TestCase):
    def test_favorite_is_synced_and_recent_is_not(self):
        conn = Connection("a", "h", favorite=True)
        item = syncing.item_from_obj("connection", conn, None)
        self.assertIs(item.data["favorite"], True)
        self.assertNotIn("recent", item.data)


class QuickGuiTests(GuiTestCase):
    def setUp(self):
        super().setUp()
        self.cfg = _cfg()
        storage.save(self.cfg, self.path)

    def _open(self):
        w = self._window()
        self.addCleanup(w.close)
        return w

    def _entries(self, w):
        out = []

        def walk(item):
            for i in range(item.childCount()):
                walk(item.child(i))
            out.append(item)

        for i in range(w.tree.topLevelItemCount()):
            walk(w.tree.topLevelItem(i))
        return out

    def test_favorite_toggle_saves_and_shows_head(self):
        from sesam.gui.main_window import FAV_ROLE

        w = self._open()
        self.assertFalse(any(it.data(0, FAV_ROLE) == "head" for it in self._entries(w)))
        conn = w.cfg.connections[0]
        w.toggle_favorite([conn])
        self.assertTrue(storage.load(self.path).connections[0].favorite)
        roles = [it.data(0, FAV_ROLE) for it in self._entries(w)]
        self.assertEqual(roles.count("head"), 1)
        self.assertEqual(roles.count("entry"), 1)
        w.toggle_favorite([conn])
        self.assertFalse(storage.load(self.path).connections[0].favorite)
        self.assertFalse(any(it.data(0, FAV_ROLE) == "head" for it in self._entries(w)))

    def test_touch_records_recent_locally(self):
        w = self._open()
        conn = w.cfg.connections[1]
        before = time.time()
        w._touch(conn)
        self.assertGreaterEqual(w.cfg.recent[conn.id], before)
        self.assertIn(conn.id, storage.load(self.path).recent)

    def test_duplicate_is_not_a_favorite(self):
        w = self._open()
        conn = w.cfg.connections[0]
        conn.favorite = True
        w.refresh()
        w._select_connection(conn.id)
        seen = []

        class FakeDialog:
            def __init__(self, cfg, clone, **_kw):
                seen.append(clone)
                self.name = type("N", (), {"selectAll": lambda self: None})()

            def exec(self):
                return False

        from unittest import mock
        with mock.patch("sesam.gui.main_window.ConnectionDialog", FakeDialog):
            w.duplicate_selected()
        self.assertEqual(len(seen), 1)
        self.assertFalse(seen[0].favorite)

    def test_launcher_filters_and_chooses(self):
        from PySide6.QtCore import Qt
        from sesam.gui.quick_dialog import QuickLauncher

        toggled = []
        dlg = QuickLauncher(self.cfg, toggled.append, None)
        self.addCleanup(dlg.close)
        dlg.input.setText("nas")
        self.app.processEvents()
        rows = [dlg.list.item(i) for i in range(dlg.list.count()) if dlg.list.item(i).flags() != Qt.ItemFlag.NoItemFlags]
        self.assertEqual(len(rows), 1)
        dlg._choose("files")
        self.assertEqual((dlg.action, dlg.conn_id), ("files", self.cfg.connections[2].id))


if __name__ == "__main__":
    unittest.main()
