"""Der Einklapp-Zustand der Gruppen überlebt einen Neustart."""
import json
import unittest
from unittest import mock

from gui_base import GuiTestCase

from sesam import storage
from sesam.models import Config, Connection


def make_config(collapsed=()):
    return Config(
        groups=["G1", "G1/Sub", "G2"],
        connections=[Connection("a", "h1", group="G1/Sub"), Connection("b", "h2", group="G2"), Connection("c", "h3")],
        collapsed_groups=list(collapsed),
    )


class StoredStateTests(unittest.TestCase):
    """Reiner Anzeigezustand: Fehler darin dürfen den Start nie verhindern."""

    def test_roundtrip_and_tolerance(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "config.json"
            storage.save(make_config(["G2", "G1/Sub"]), p)
            self.assertEqual(storage.load(p).collapsed_groups, ["G2", "G1/Sub"])
            for raw, expected in (("abc", []), (None, []), ({"a": 1}, []), (["A", 1, None, "B"], ["A", "B"])):
                data = json.loads(p.read_text(encoding="utf-8"))
                data["collapsed_groups"] = raw
                p.write_text(json.dumps(data), encoding="utf-8")
                self.assertEqual(storage.load(p).collapsed_groups, expected, raw)
            data.pop("collapsed_groups")  # alte Konfiguration ohne das Feld
            p.write_text(json.dumps(data), encoding="utf-8")
            self.assertEqual(storage.load(p).collapsed_groups, [])


class GroupStateTests(GuiTestCase):
    def test_state_is_restored_after_restart(self):
        storage.save(make_config(), self.path)
        w = self._window()
        for path in ("G1", "G1/Sub", "G2"):
            self.assertTrue(self._group_item(w, path).isExpanded(), "ohne gespeicherten Zustand sind alle Gruppen offen")
        self._group_item(w, "G2").setExpanded(False)
        self._group_item(w, "G1/Sub").setExpanded(False)
        w.close()  # beim Schließen wird der Zustand gesichert, auch wenn der Zeitgeber noch nicht abgelaufen ist
        self.assertEqual(storage.load(self.path).collapsed_groups, ["G1/Sub", "G2"])

        w2 = self._window()  # „Neustart“
        self.assertFalse(self._group_item(w2, "G2").isExpanded())
        self.assertFalse(self._group_item(w2, "G1/Sub").isExpanded())
        self.assertTrue(self._group_item(w2, "G1").isExpanded())
        self._group_item(w2, "G2").setExpanded(True)  # wieder aufklappen
        w2.close()
        self.assertEqual(storage.load(self.path).collapsed_groups, ["G1/Sub"])

    def test_saved_shortly_after_toggle_without_syncing(self):
        storage.save(make_config(), self.path)
        w = self._window()
        with mock.patch.object(w.sync, "schedule") as schedule:
            self._group_item(w, "G2").setExpanded(False)
            self.assertTrue(w._collapse_timer.isActive())
            w._flush_collapsed()  # so, als wäre der Zeitgeber abgelaufen
            self.assertEqual(storage.load(self.path).collapsed_groups, ["G2"])
            w.close()
        schedule.assert_not_called()  # reiner Anzeigezustand löst keine Synchronisation aus

    def test_unchanged_state_does_not_rewrite_the_file(self):
        storage.save(make_config(["G2"]), self.path)
        w = self._window()
        before = self.path.stat().st_mtime_ns
        with mock.patch.object(storage, "save") as save:
            w._flush_collapsed()
        save.assert_not_called()
        self.assertEqual(self.path.stat().st_mtime_ns, before)

    def test_search_does_not_change_state(self):
        storage.save(make_config(), self.path)
        w = self._window()
        w.search.setText("a")  # während der Suche sind Gruppen automatisch offen
        self._group_item(w, "G1/Sub").setExpanded(False)
        self.assertEqual(w.collapsed, set())

    def test_stale_entries_are_dropped(self):
        storage.save(make_config(["Gibt es nicht", "G2"]), self.path)
        w = self._window()
        self.assertEqual(w.collapsed, {"G2"})
        w._save()
        self.assertEqual(storage.load(self.path).collapsed_groups, ["G2"])

    def test_state_follows_rename_move_and_delete(self):
        from PySide6.QtWidgets import QInputDialog, QMessageBox

        storage.save(Config(groups=["G2", "G2/X", "G1"], collapsed_groups=["G2", "G2/X"]), self.path)
        w = self._window()
        with mock.patch.object(QInputDialog, "getText", lambda *a, **k: ("G9", True)):
            w.rename_group("G2")
        self.assertEqual(w.collapsed, {"G9", "G9/X"})
        self.assertFalse(self._group_item(w, "G9").isExpanded())
        self.assertEqual(storage.load(self.path).collapsed_groups, ["G9", "G9/X"])

        w._move("group", "G9", "G1")  # per Drag & Drop in eine andere Gruppe
        self.assertEqual(w.collapsed, {"G1/G9", "G1/G9/X"})
        self.assertFalse(self._group_item(w, "G1/G9/X").isExpanded())

        with mock.patch.object(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes):
            w.delete_group("G1/G9")  # Untergruppen rücken hoch
        self.assertEqual(w.collapsed, {"G1/X"})
        self.assertEqual(storage.load(self.path).collapsed_groups, ["G1/X"])

    def test_new_group_unfolds_its_parents_and_is_saved(self):
        from PySide6.QtWidgets import QInputDialog

        storage.save(make_config(["G1", "G1/Sub"]), self.path)
        w = self._window()
        w.tree.setCurrentItem(self._group_item(w, "G1/Sub"))
        with mock.patch.object(QInputDialog, "getText", lambda *a, **k: ("Neu", True)):
            w.new_group()
        self.assertEqual(w.collapsed, set())  # Eltern wurden aufgeklappt, damit man die neue Gruppe sieht
        self.assertEqual(storage.load(self.path).collapsed_groups, [])


if __name__ == "__main__":
    unittest.main()
