"""Farben und Tags: Datenmodell, Speichern, Gruppen, Synchronisation."""
import json
import tempfile
import unittest
from pathlib import Path

from sesam import colors, storage, tags
from sesam import groups as grp
from sesam.models import Config, Connection
from sesam.syncing import merge_map


class TagTests(unittest.TestCase):
    def test_parse_splits_on_comma_and_semicolon_and_removes_duplicates(self):
        self.assertEqual(tags.parse(" prod, Kunde  A ;db,PROD,, "), ["prod", "Kunde A", "db"])
        self.assertEqual(tags.parse(""), [])

    def test_format_roundtrip(self):
        self.assertEqual(tags.parse(tags.format(["a", "b c"])), ["a", "b c"])

    def test_all_tags_sorted_with_most_common_spelling(self):
        cfg = Config(connections=[
            Connection("1", "h", tags=["Prod", "web"]),
            Connection("2", "h", tags=["prod"]),
            Connection("3", "h", tags=["prod", "db"]),
        ])
        self.assertEqual(tags.all_tags(cfg), ["db", "prod", "web"])


class ModelTests(unittest.TestCase):
    def test_connection_from_dict_cleans_damaged_values(self):
        c = Connection.from_dict({"name": "a", "host": "h", "tags": "kein-liste", "color": 5})
        self.assertEqual((c.tags, c.color), ([], ""))
        c = Connection.from_dict({"name": "a", "host": "h", "tags": ["x", 3, " x ", "", "y"], "color": "red"})
        self.assertEqual((c.tags, c.color), (["x", "y"], "red"))

    def test_old_files_without_fields_load(self):
        c = Connection.from_dict({"name": "a", "host": "h"})
        self.assertEqual((c.tags, c.color), ([], ""))

    def test_storage_roundtrip_and_robust_group_colors(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.json"
            cfg = Config(connections=[Connection("a", "h", color="blue", tags=["t1", "t2"])], group_colors={"Kunde": "red"})
            storage.save(cfg, path)
            got = storage.load(path)
            self.assertEqual((got.connections[0].color, got.connections[0].tags, got.group_colors), ("blue", ["t1", "t2"], {"Kunde": "red"}))
            data = json.loads(path.read_text(encoding="utf-8"))
            data["group_colors"] = ["kaputt"]
            path.write_text(json.dumps(data), encoding="utf-8")
            self.assertEqual(storage.load(path).group_colors, {})
            data["group_colors"] = {"A": "red", "B": 3, "": "blue", "C": ""}
            path.write_text(json.dumps(data), encoding="utf-8")
            self.assertEqual(storage.load(path).group_colors, {"A": "red"})

    def test_palette(self):
        self.assertTrue(colors.valid("blue"))
        self.assertFalse(colors.valid("neon"))
        self.assertIsNone(colors.hex_of("neon"))
        self.assertIsNone(colors.hex_of(""))
        self.assertTrue(all(h.startswith("#") and len(h) == 7 for _n, h in colors.PALETTE.values()))


class GroupColorTests(unittest.TestCase):
    def cfg(self):
        return Config(
            connections=[Connection("a", "h", group="Kunde/Prod")],
            group_colors={"Kunde": "blue", "Kunde/Prod": "red", "Privat": "green"},
        )

    def test_set_and_clear(self):
        cfg = self.cfg()
        grp.set_color(cfg, "Privat", "purple")
        self.assertEqual(cfg.group_colors["Privat"], "purple")
        grp.set_color(cfg, "Privat", "")
        self.assertNotIn("Privat", cfg.group_colors)

    def test_rename_moves_colors_of_group_and_subgroups(self):
        cfg = self.cfg()
        grp.rename_group(cfg, "Kunde", "Firma/Kunde")
        self.assertEqual(cfg.group_colors, {"Firma/Kunde": "blue", "Firma/Kunde/Prod": "red", "Privat": "green"})

    def test_delete_drops_color_but_keeps_subgroup_color(self):
        cfg = self.cfg()
        grp.delete_group(cfg, "Kunde")
        self.assertEqual(cfg.group_colors, {"Prod": "red", "Privat": "green"})  # „Kunde“ blau darf nicht auf die Wurzel übergehen

    def test_delete_subgroup_does_not_overwrite_parent_color(self):
        cfg = self.cfg()
        grp.delete_group(cfg, "Kunde/Prod")
        self.assertEqual(cfg.group_colors, {"Kunde": "blue", "Privat": "green"})

    def test_move_onto_existing_group_keeps_moved_color(self):
        cfg = Config(group_colors={"A": "red", "B": "green"}, groups=["A", "B"])
        grp.rename_group(cfg, "A", "B")
        self.assertEqual(cfg.group_colors, {"B": "red"})


class MergeMapTests(unittest.TestCase):
    def test_cases(self):
        self.assertEqual(merge_map({"a": "x"}, {"a": "y"}, {"a": "x"}), {"a": "y"})   # nur lokal geändert
        self.assertEqual(merge_map({"a": "x"}, {"a": "x"}, {"a": "z"}), {"a": "z"})   # nur Server geändert
        self.assertEqual(merge_map({"a": "x"}, {"a": "y"}, {"a": "z"}), {"a": "y"})   # beide: lokal gewinnt
        self.assertEqual(merge_map({"a": "x"}, {}, {"a": "x"}), {})                   # lokal entfernt
        self.assertEqual(merge_map({"a": "x"}, {"a": "x"}, {}), {})                   # am Server entfernt
        self.assertEqual(merge_map({}, {"a": "x"}, {"b": "y"}), {"a": "x", "b": "y"})  # neu auf beiden Seiten


if __name__ == "__main__":
    unittest.main()
