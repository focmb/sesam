"""Markierung der Verbindungsarten (SSH, VNC, RDP): Spalte „Typ“, Symbol, Ziel, Tooltip und Suchfilter „typ:“."""
import json
import unittest
from unittest import mock

from gui_base import GuiTestCase

from sesam import remote, storage
from sesam.models import Config, Connection


class KindsTests(unittest.TestCase):
    def test_kinds(self):
        self.assertEqual(remote.kinds(Connection("a", "h")), ["SSH"])
        self.assertEqual(remote.kinds(Connection("a", "h", remote="vnc")), ["SSH", "VNC"])
        self.assertEqual(remote.kinds(Connection("a", "h", remote="rdp", remote_mode="tunnel")), ["SSH", "RDP"])
        self.assertEqual(remote.kinds(Connection("a", "h", remote="vnc", remote_mode="direct")), ["VNC"])
        self.assertEqual(remote.kinds(Connection("a", "h", remote="rdp", remote_mode="direct")), ["RDP"])
        self.assertEqual(remote.kinds(Connection("a", "h", remote="", remote_mode="direct")), ["SSH"])  # ohne Fernzugriff gibt es kein „direkt“


class TypeColumnTests(GuiTestCase):
    def setUp(self):
        super().setUp()
        self.ssh = Connection("alpha", "a.example")
        self.vnc = Connection("bravo", "b.example", remote="vnc", remote_mode="tunnel")
        self.rdp = Connection("charlie", "c.example", remote="rdp", remote_mode="tunnel")
        self.dvnc = Connection("delta", "10.0.0.4", remote="vnc", remote_mode="direct", remote_port=5901)
        self.drdp = Connection("echo", "10.0.0.5", remote="rdp", remote_mode="direct")
        storage.save(Config(connections=[self.ssh, self.vnc, self.rdp, self.dvnc, self.drdp]), self.path)
        self.w = self._window()
        self.addCleanup(self.w.close)

    def item(self, conn):
        return self.w._find_item(conn.id)

    def shown(self):
        return sorted(c.name for c in self.w.cfg.connections if self.item(c) is not None)

    def test_column_is_next_to_the_name_and_visible_by_default(self):
        header = self.w.tree.header()
        self.assertEqual(self.w.tree.headerItem().text(5), "Typ")
        self.assertEqual(header.visualIndex(5), 1)
        self.assertFalse(header.isSectionHidden(5))
        self.assertEqual(self.w.cfg.hidden_columns, ["os"])

    def test_texts_and_badge_data(self):
        from sesam.gui.type_badges import KIND_ROLE

        expected = [(self.ssh, ["SSH"]), (self.vnc, ["SSH", "VNC"]), (self.rdp, ["SSH", "RDP"]), (self.dvnc, ["VNC"]), (self.drdp, ["RDP"])]
        for conn, kinds in expected:
            self.assertEqual(self.item(conn).data(5, KIND_ROLE), kinds, conn.name)
            self.assertEqual(self.item(conn).text(5), " · ".join(kinds), conn.name)

    def test_target_of_direct_connections_is_the_remote_port(self):
        self.assertEqual(self.item(self.dvnc).text(1), "vnc://10.0.0.4:5901")
        self.assertEqual(self.item(self.drdp).text(1), "rdp://10.0.0.5:3389")
        self.assertEqual(self.item(self.ssh).text(1), "a.example")
        self.assertEqual(self.item(self.vnc).text(1), "b.example")  # bei einem Tunnel bleibt es die SSH-Adresse

    def test_tooltips_explain_the_kind(self):
        self.assertIn("SSH", self.item(self.ssh).toolTip(5))
        self.assertIn("Tunnel", self.item(self.vnc).toolTip(5))
        self.assertIn("5900", self.item(self.vnc).toolTip(5))
        self.assertIn("ohne SSH", self.item(self.dvnc).toolTip(5))
        self.assertIn("5901", self.item(self.dvnc).toolTip(5))

    def test_screen_symbol_for_devices_without_ssh(self):
        from sesam.gui import style

        names = {}
        real = style.icon
        with mock.patch.object(style, "icon", side_effect=lambda name, *a, **k: names.setdefault(len(names), name) or real(name, *a, **k)):
            self.w._server_icon(self.dvnc, False)
            self.w._server_icon(self.ssh, False)
            self.w._server_icon(self.vnc, False)
        self.assertEqual(list(names.values()), ["monitor", "server", "server"])

    def test_search_filter(self):
        cases = {
            "typ:vnc": ["bravo", "delta"],
            "typ:rdp": ["charlie", "echo"],
            "typ:ssh": ["alpha", "bravo", "charlie"],
            "TYP:VNC": ["bravo", "delta"],
            "typ:vnc bravo": ["bravo"],
            "typ:vnc typ:rdp": [],
            "typ:xyz": [],  # unbekannte Art: gewöhnlicher Suchtext
            "alpha": ["alpha"],
            "": ["alpha", "bravo", "charlie", "delta", "echo"],
        }
        for text, names in cases.items():
            with self.subTest(text=text):
                self.w.search.setText(text)
                self.assertEqual(self.shown(), names)

    def test_column_can_be_hidden_and_remembered(self):
        self.w.set_column_visible("type", False)
        self.assertTrue(self.w.tree.header().isSectionHidden(5))
        self.assertEqual(storage.load(self.path).hidden_columns, ["type", "os"])

    def test_badges_are_painted(self):
        self.w.resize(1100, 500)
        self.app.processEvents()
        image = self.w.tree.viewport().grab().toImage()
        self.assertFalse(image.isNull())
        header = self.w.tree.header()
        x = header.sectionViewportPosition(5) + 20
        y = self.w.tree.visualItemRect(self.item(self.vnc)).center().y()
        colors = {image.pixelColor(x + dx, y).name() for dx in range(0, 60)}
        self.assertGreater(len(colors), 1)  # nicht nur der Hintergrund: die Abzeichen sind zu sehen

    def test_old_widths_without_type_column_get_the_default(self):
        data = json.loads(self.path.read_text(encoding="utf-8"))
        data["column_widths"] = [300, 200, 100, 150, 180]
        self.path.write_text(json.dumps(data), encoding="utf-8")
        w = self._window()
        self.addCleanup(w.close)
        header = w.tree.header()
        self.assertEqual([header.sectionSize(i) for i in range(4)], [300, 200, 100, 150])
        self.assertEqual(header.sectionSize(5), w.DEFAULT_WIDTHS[5])


if __name__ == "__main__":
    unittest.main()
