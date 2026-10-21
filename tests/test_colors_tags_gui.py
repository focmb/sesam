"""Farben und Tags in der Oberfläche: Icons im Baum, Tags-Spalte, Suche, Menü, Verbindungsdialog."""
import unittest

from gui_base import GuiTestCase

from sesam import colors, storage
from sesam.models import Config, Connection


def icon_color(item):
    """Häufigste deckende Farbe des Icons (die Linienfarbe)."""
    image = item.icon(0).pixmap(18, 18).toImage()
    counts = {}
    for y in range(image.height()):
        for x in range(image.width()):
            c = image.pixelColor(x, y)
            if c.alpha() > 100:
                counts[c.name()] = counts.get(c.name(), 0) + 1
    return max(counts, key=counts.get) if counts else None


def near(a, b, tol=6):
    """Farben stimmen bis auf Rundung beim Zeichnen überein."""
    if a is None or b is None:
        return False
    return all(abs(int(a[i:i + 2], 16) - int(b[i:i + 2], 16)) <= tol for i in (1, 3, 5))


class ColorTagTreeTests(GuiTestCase):
    def setUp(self):
        super().setUp()
        self.red = Connection("web", "h1", group="Kunde", color="red", tags=["prod", "Web"])
        self.plain = Connection("nas", "h2", tags=["privat"])
        storage.save(Config(connections=[self.red, self.plain], group_colors={"Kunde": "blue"}), self.path)
        self.w = self._window()
        self.addCleanup(self.w.close)

    def conn(self, name):
        return next(c for c in self.w.cfg.connections if c.name == name)

    def item(self, name):
        from PySide6.QtWidgets import QTreeWidgetItemIterator

        it = QTreeWidgetItemIterator(self.w.tree)
        while it.value():
            if it.value().text(0) == name:
                return it.value()
            it += 1
        self.fail(name)

    def test_icons_use_connection_and_group_colors(self):
        self.assertTrue(near(icon_color(self.item("web")), colors.hex_of("red")))
        self.assertTrue(near(icon_color(self.item("Kunde")), colors.hex_of("blue")))
        self.assertFalse(near(icon_color(self.item("nas")), colors.hex_of("red")))  # ohne Farbe: Standard

    def test_unknown_color_key_falls_back_to_default(self):
        self.conn("nas").color = "neon"  # z. B. von einer neueren Version geschrieben
        self.w.refresh()
        self.assertFalse(any(near(icon_color(self.item("nas")), c[1]) for c in colors.PALETTE.values()))

    def test_tags_column_and_header(self):
        self.assertEqual(self.w.tree.headerItem().text(3), "Tags")
        self.assertEqual(self.item("web").text(3), "prod, Web")

    def test_search_finds_tags(self):
        self.w.search.setText("privat")
        names = [self.w.tree.topLevelItem(i).text(0) for i in range(self.w.tree.topLevelItemCount())]
        self.assertEqual(names, ["nas"])
        self.w.search.setText("WEB")  # Groß-/Kleinschreibung egal; trifft auch den Namen
        self.assertEqual(self.item("web").text(0), "web")

    def test_set_connection_color_persists_and_keeps_selection(self):
        self.w.set_connection_color(self.conn("nas"), "green")
        self.assertEqual(storage.load(self.path).connection_by_id(self.conn("nas").id).color, "green")
        self.assertTrue(near(icon_color(self.item("nas")), colors.hex_of("green")))
        self.assertEqual(self.w._selected().id, self.conn("nas").id)
        self.w.set_connection_color(self.conn("nas"), "")
        self.assertEqual(storage.load(self.path).connection_by_id(self.conn("nas").id).color, "")

    def test_set_group_color_persists(self):
        self.w.set_group_color("Kunde", "purple")
        self.assertEqual(storage.load(self.path).group_colors, {"Kunde": "purple"})
        self.w.set_group_color("Kunde", "")
        self.assertEqual(storage.load(self.path).group_colors, {})

    def test_group_color_follows_rename_via_tree_data(self):
        from sesam import groups as grp

        grp.rename_group(self.w.cfg, "Kunde", "Firma")
        self.w.refresh()
        self.assertTrue(near(icon_color(self.item("Firma")), colors.hex_of("blue")))

    def test_color_menu_lists_palette_and_marks_current(self):
        from PySide6.QtWidgets import QMenu

        picked = []
        menu = QMenu()
        self.w._color_menu(menu, "red", picked.append)
        sub = menu.findChild(QMenu)
        labels = [a.text() for a in sub.actions()]
        self.assertEqual(labels, ["Keine"] + [name for name, _h in colors.PALETTE.values()])
        self.assertEqual([a.text() for a in sub.actions() if a.isChecked()], ["Rot"])
        next(a for a in sub.actions() if a.text() == "Lila").trigger()
        sub.actions()[0].trigger()
        self.assertEqual(picked, ["purple", ""])

    def test_duplicate_keeps_color_and_tags(self):
        from unittest import mock

        self.w.tree.setCurrentItem(self.item("web"))
        with mock.patch.object(self.w, "edit_selected"):
            self.w.duplicate_selected()
        copy = next(c for c in self.w.cfg.connections if c.name == "web (Kopie)")
        self.assertEqual((copy.color, copy.tags), ("red", ["prod", "Web"]))


class ConnectionDialogColorTagTests(GuiTestCase):
    def dialog(self, conn=None, cfg=None):
        from sesam.gui.connection_dialog import ConnectionDialog

        dlg = ConnectionDialog(cfg or Config(), conn)
        self.addCleanup(dlg.close)
        return dlg

    def test_roundtrip(self):
        conn = Connection("a", "h", color="teal", tags=["x", "y"])
        dlg = self.dialog(conn, Config(connections=[conn]))
        self.assertEqual(dlg.color.currentData(), "teal")
        self.assertEqual(dlg.tags.text(), "x, y")
        dlg.color.setCurrentIndex(dlg.color.findData("orange"))
        dlg.tags.setText("neu ; Zwei,, neu")
        dlg._accept()
        self.assertEqual((conn.color, conn.tags), ("orange", ["neu", "Zwei"]))

    def test_no_color_and_empty_tags(self):
        conn = Connection("a", "h", color="red", tags=["x"])
        dlg = self.dialog(conn, Config(connections=[conn]))
        dlg.color.setCurrentIndex(0)
        dlg.tags.setText("")
        dlg._accept()
        self.assertEqual((conn.color, conn.tags), ("", []))

    def test_tag_completion_after_comma(self):
        other = Connection("o", "h", tags=["produktion", "privat", "test"])
        dlg = self.dialog(None, Config(connections=[other]))
        completer = dlg.tags.completer()
        self.assertEqual(completer.splitPath("web, pr"), ["pr"])
        dlg.tags.setText("web, pr")
        completer.setCompletionPrefix("pr")
        self.assertEqual(completer.completionCount(), 2)
        idx = completer.completionModel().index(0, 0)
        self.assertEqual(completer.pathFromIndex(idx), "web, privat")


if __name__ == "__main__":
    unittest.main()
