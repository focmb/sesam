"""Mehrfachauswahl im Verbindungsbaum: Löschen, Farbe, Verschieben."""
import unittest
from unittest import mock

from gui_base import GuiTestCase

from sesam import passwords, storage
from sesam.models import Config, Connection


class MultiSelectTests(GuiTestCase):
    def setUp(self):
        super().setUp()
        self.conns = [Connection(n, f"{n}.example", group=g) for n, g in
                      (("a", ""), ("b", ""), ("c", "Prod"), ("d", "Prod"), ("e", ""))]
        storage.save(Config(connections=self.conns, groups=["Prod", "Leer"]), self.path)
        self.w = self._window()
        self.addCleanup(self.w.close)
        self.w.tree.expandAll()

    def item(self, name):
        from PySide6.QtWidgets import QTreeWidgetItemIterator

        it = QTreeWidgetItemIterator(self.w.tree)
        while it.value():
            if it.value().text(0) == name:
                return it.value()
            it += 1
        self.fail(name)

    def select(self, *names):
        self.w.tree.clearSelection()
        for n in names:
            self.item(n).setSelected(True)
        self.w.tree.setCurrentItem(self.item(names[-1]), 0, __import__("PySide6.QtCore", fromlist=["QItemSelectionModel"]).QItemSelectionModel.SelectionFlag.NoUpdate)

    def names(self):
        return sorted(c.name for c in self.w.cfg.connections)

    def test_tree_allows_extended_selection(self):
        from PySide6.QtWidgets import QAbstractItemView

        self.assertEqual(self.w.tree.selectionMode(), QAbstractItemView.SelectionMode.ExtendedSelection)

    def test_selected_connections_ignore_groups_and_keep_order(self):
        self.select("a", "Prod", "c")
        self.assertEqual([c.name for c in self.w._selected_connections()], ["a", "c"])

    def test_delete_multiple_after_confirmation(self):
        from PySide6.QtWidgets import QMessageBox

        deleted = []
        self.select("a", "c", "d")
        asked = []

        def yes(*args):
            asked.append(args[2])
            return QMessageBox.StandardButton.Yes

        with mock.patch.object(QMessageBox, "question", yes), \
                mock.patch.object(passwords, "delete_password", lambda i: deleted.append(i)):
            self.w.cfg.connections[0].use_password = True
            self.w.delete_selected()
        self.assertEqual(self.names(), ["b", "e"])
        self.assertIn("3 Verbindungen", asked[0])
        self.assertTrue(all(n in asked[0] for n in ("• a", "• c", "• d")))
        self.assertEqual(len(deleted), 1)
        self.assertEqual(sorted(c.name for c in storage.load(self.path).connections), ["b", "e"])

    def test_delete_declined_keeps_everything(self):
        from PySide6.QtWidgets import QMessageBox

        self.select("a", "b")
        with mock.patch.object(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.No):
            self.w.delete_selected()
        self.assertEqual(len(self.w.cfg.connections), 5)

    def test_delete_with_groups_in_selection_only_removes_connections(self):
        from PySide6.QtWidgets import QMessageBox

        self.select("Prod", "a", "b")
        with mock.patch.object(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Yes):
            self.w.delete_selected()
        self.assertEqual(self.names(), ["c", "d", "e"])
        self.assertIn("Prod", self.w.cfg.groups)

    def test_single_delete_keeps_old_message(self):
        from PySide6.QtWidgets import QMessageBox

        self.w.tree.setCurrentItem(self.item("b"))
        asked = []
        with mock.patch.object(QMessageBox, "question", lambda *a: asked.append(a[2]) or QMessageBox.StandardButton.No):
            self.w.delete_selected()
        self.assertEqual(asked, ["Verbindung „b“ wirklich löschen?"])

    def test_password_error_is_reported_once_but_all_are_deleted(self):
        from PySide6.QtWidgets import QMessageBox

        for c in self.w.cfg.connections[:2]:
            c.use_password = True
        warnings = []

        def boom(i):
            raise passwords.PasswordError("kaputt")

        self.select("a", "b")
        with mock.patch.object(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Yes), \
                mock.patch.object(passwords, "delete_password", boom), \
                mock.patch.object(QMessageBox, "warning", lambda *a: warnings.append(a[2])):
            self.w.delete_selected()
        self.assertEqual(self.names(), ["c", "d", "e"])
        self.assertEqual(len(warnings), 1)
        self.assertIn("a: kaputt", warnings[0])
        self.assertIn("b: kaputt", warnings[0])

    def test_set_color_for_all_selected_keeps_selection(self):
        self.select("a", "c", "e")
        self.w.set_connections_color(self.w._selected_connections(), "red")
        colors = {c.name: c.color for c in self.w.cfg.connections}
        self.assertEqual(colors, {"a": "red", "b": "", "c": "red", "d": "", "e": "red"})
        self.assertEqual(sorted(c.name for c in self.w._selected_connections()), ["a", "c", "e"])
        self.assertEqual({c.name: c.color for c in storage.load(self.path).connections}["c"], "red")
        self.w.set_connections_color(self.w._selected_connections(), "")
        self.assertTrue(all(c.color == "" for c in self.w.cfg.connections))

    def test_context_menu_with_several_connections_offers_color_and_delete(self):
        self.select("a", "b")
        seen = {}

        def fake_exec(menu, *a):
            seen["texts"] = [x.text() for x in menu.actions()]
            seen["color"] = [x for x in menu.actions() if x.text() == "Farbe"]
            return None

        with mock.patch.object(type(self.w), "_popup", lambda w, menu, pos: fake_exec(menu)):
            pos = self.w.tree.visualItemRect(self.item("a")).center()
            self.w._context_menu(pos)
        self.assertIn("2 Verbindungen ausgewählt", seen["texts"])
        self.assertIn("2 Verbindungen löschen", seen["texts"])
        self.assertEqual(len(seen["color"]), 1)
        self.assertNotIn("Bearbeiten", seen["texts"])
        self.assertEqual(len(self.w._selected_connections()), 2)  # Rechtsklick behält die Auswahl

    def test_right_click_outside_selection_selects_that_item_only(self):
        self.select("a", "b")
        with mock.patch.object(type(self.w), "_popup", lambda *a: None):
            self.w._context_menu(self.w.tree.visualItemRect(self.item("e")).center())
        self.assertEqual([c.name for c in self.w._selected_connections()], ["e"])

    def test_dropping_a_selected_connection_moves_all_selected(self):
        from PySide6.QtCore import QMimeData, QPointF, Qt
        from PySide6.QtGui import QDropEvent

        self.select("a", "b", "e")
        self.w.tree.setCurrentItem(self.item("b"), 0, __import__("PySide6.QtCore", fromlist=["QItemSelectionModel"]).QItemSelectionModel.SelectionFlag.NoUpdate)
        target = self.w.tree.visualItemRect(self.item("Prod")).center()
        event = QDropEvent(QPointF(target), Qt.DropAction.MoveAction, QMimeData(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        self.w.tree.dropEvent(event)
        for _ in range(10):
            self.app.processEvents()
        self.assertEqual({c.name: c.group for c in self.w.cfg.connections},
                         {"a": "Prod", "b": "Prod", "c": "Prod", "d": "Prod", "e": "Prod"})

    def test_dropping_an_unselected_connection_moves_only_it(self):
        from PySide6.QtCore import QMimeData, QPointF, Qt
        from PySide6.QtGui import QDropEvent

        self.select("a", "b")
        self.w.tree.setCurrentItem(self.item("e"), 0, __import__("PySide6.QtCore", fromlist=["QItemSelectionModel"]).QItemSelectionModel.SelectionFlag.NoUpdate)
        self.item("e").setSelected(False)
        target = self.w.tree.visualItemRect(self.item("Prod")).center()
        event = QDropEvent(QPointF(target), Qt.DropAction.MoveAction, QMimeData(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        self.w.tree.dropEvent(event)
        for _ in range(10):
            self.app.processEvents()
        self.assertEqual({c.name: c.group for c in self.w.cfg.connections}["e"], "Prod")
        self.assertEqual({c.name: c.group for c in self.w.cfg.connections}["a"], "")


if __name__ == "__main__":
    unittest.main()
