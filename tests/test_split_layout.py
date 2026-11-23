"""Verbindungsliste links bleibt sichtbar; rechts Sitzungen oder (ohne Sitzung) die zuletzt verwendeten Verbindungen."""
import json
import time
import unittest

from gui_base import GuiTestCase

from sesam import storage
from sesam.models import Config, Connection


class StorageTests(unittest.TestCase):
    def test_invalid_list_width_is_ignored(self):
        for bad in ("x", None, True, 10, 999999, [300], {"a": 1}):
            self.assertEqual(storage._from_data({"list_width": bad}).list_width, 0, bad)
        self.assertEqual(storage._from_data({"list_width": 480.0}).list_width, 480)
        self.assertEqual(storage._from_data({}).list_width, 0)

    def test_roundtrip(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.json"
            storage.save(Config(list_width=512), path)
            self.assertEqual(storage.load(path).list_width, 512)
            self.assertIn("list_width", json.loads(path.read_text()))


class LayoutTests(GuiTestCase):
    def open(self, connections, recent=None, **kw):
        storage.save(Config(connections=connections, recent=recent or {}, **kw), self.path)
        w = self._window()
        self.addCleanup(w.close)
        return w

    def test_list_is_left_of_the_terminal_area(self):
        w = self.open([Connection("a", "h")])
        self.assertIs(w.splitter.widget(1), w.stack)
        self.assertTrue(w.tree.isVisible())
        self.assertEqual(w.tabs.count(), 0)  # in den Reitern stehen nur Sitzungen
        self.assertIs(w.stack.currentWidget(), w.start_page)

    def test_saved_width_is_restored_and_saved_on_close(self):
        w = self.open([Connection("a", "h")], list_width=450)
        self.assertEqual(w.splitter.sizes()[0], 450)
        w.splitter.setSizes([380, 620])
        self.app.processEvents()
        w.close()
        self.assertEqual(storage.load(self.path).list_width, w.splitter.sizes()[0])

    def test_start_page_shows_the_ten_latest_connections_newest_first(self):
        now = time.time()
        conns = [Connection(f"srv{i:02d}", f"h{i}") for i in range(14)]
        recent = {c.id: now - 100 * i for i, c in enumerate(conns)}
        recent["gelöscht"] = now  # unbekannte IDs werden übersprungen
        w = self.open(conns, recent)
        names = [w.start_list.item(i).text().splitlines()[0] for i in range(w.start_list.count())]
        self.assertEqual(names, [f"srv{i:02d}" for i in range(10)])

    def test_start_page_hint_without_history(self):
        w = self.open([Connection("a", "h")])
        self.assertEqual(w.start_list.count(), 0)
        self.assertFalse(w.start_hint.isHidden())

    def test_activating_an_entry_opens_that_connection(self):
        from unittest import mock

        conn = Connection("a", "h")
        w = self.open([conn], {conn.id: time.time()})
        with mock.patch.object(w, "_open_connection") as opened, mock.patch("sesam.launcher.is_open", return_value=False):
            w._start_item_activated(w.start_list.item(0))
        opened.assert_called_once()
        self.assertEqual(opened.call_args.args[0].id, conn.id)

    def test_touch_updates_the_start_page(self):
        a, b = Connection("a", "h"), Connection("b", "h2")
        w = self.open([a, b], {a.id: time.time() - 500})
        self.assertEqual(w.start_list.count(), 1)
        w._touch(b)
        self.assertEqual([w.start_list.item(i).text().splitlines()[0] for i in range(w.start_list.count())], ["b", "a"])


if __name__ == "__main__":
    unittest.main()
