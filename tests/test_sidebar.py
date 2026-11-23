"""Werkzeugzeile über der Verbindungsliste: Anordnung, Tastenkürzel, Zustand der Knöpfe, Suchfeld."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication
except ImportError:  # PySide6 oder Qt-Systembibliotheken fehlen
    QApplication = None


@unittest.skipIf(QApplication is None, "PySide6 nicht verfügbar")
class SidebarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    @classmethod
    def tearDownClass(cls):
        cls._close_widgets()

    @staticmethod
    def _close_widgets():
        from PySide6.QtCore import QCoreApplication, QEvent

        for widget in QApplication.topLevelWidgets():
            widget.close()
            widget.deleteLater()
        QCoreApplication.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        QCoreApplication.processEvents()

    def setUp(self):
        from sesam import passwords

        for name, fake in (("set_password", lambda i, p: None), ("get_password", lambda i: None), ("delete_password", lambda i: None)):
            patcher = mock.patch.object(passwords, name, fake)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self._close_widgets)
        self._forbid_modal_dialogs()
        self.path = Path(self.tmp.name) / "c.json"

    def _forbid_modal_dialogs(self):
        """Ein unerwartetes modales Fenster würde den Test endlos blockieren – lieber sofort fehlschlagen."""
        from PySide6.QtWidgets import QDialog, QInputDialog, QMessageBox

        def boom(*_a, **_k):
            raise AssertionError("Unerwartetes modales Fenster im Test (würde hängen)")

        for owner, names in ((QDialog, ["exec"]), (QMessageBox, ["information", "question", "warning", "critical"]),
                             (QInputDialog, ["getText", "getItem", "getInt"])):
            for name in names:
                patcher = mock.patch.object(owner, name, boom)
                patcher.start()
                self.addCleanup(patcher.stop)

    def _window(self):
        from sesam import storage
        from sesam.gui.main_window import MainWindow

        w = MainWindow(storage.load(self.path), self.path)
        w.resize(1000, 600)
        w.show()
        self.app.processEvents()
        return w

    @staticmethod
    def _pos(widget, window):
        return widget.mapTo(window, widget.rect().topLeft())

    def test_actions_form_one_row_between_search_and_list(self):
        from PySide6.QtWidgets import QToolBar

        w = self._window()
        order = list(w.TOOLBAR_ACTIONS)
        self.assertEqual(set(w.tool_buttons), set(order))
        points = [self._pos(w.tool_buttons[attr], w) for attr in order]
        xs = [p.x() for p in points]
        self.assertTrue(all(a < b for a, b in zip(xs, xs[1:])), f"nicht nebeneinander: {xs}")
        self.assertEqual(len({p.y() for p in points}), 1, "Knöpfe stehen nicht bündig")
        search_bottom = w.search.mapTo(w, w.search.rect().bottomLeft()).y()
        tree_top = w.tree.mapTo(w, w.tree.rect().topLeft()).y()
        self.assertGreater(points[0].y(), search_bottom - 1)  # unter dem Suchfeld
        self.assertLess(points[0].y(), tree_top)  # über der Liste
        # alles steht in einer Spalte links: keine zweite Leiste neben der Liste
        list_right = w.tree.mapTo(w, w.tree.rect().topRight()).x()
        self.assertLessEqual(self._pos(w.settings_button, w).x(), list_right)
        self.assertLess(list_right, w.splitter.sizes()[0] + 1)
        self.assertEqual(w.findChildren(QToolBar), [])  # die alte Werkzeugleiste ist weg
        self.assertEqual(w.tool_buttons["a_connect"].text(), w.a_connect.text())
        self.assertIn("a_files", w.tool_buttons)  # Dateien (SFTP) und Lokales Terminal stehen direkt in der Zeile
        self.assertIn("a_local", w.tool_buttons)
        self.assertGreater(self._pos(w.more_button, w).x(), xs[-1])  # „Weitere Aktionen“ und Einstellungen am rechten Ende
        self.assertGreater(self._pos(w.settings_button, w).x(), self._pos(w.more_button, w).x())

    def test_more_menu_holds_the_rarer_actions(self):
        w = self._window()
        menu_actions = [a for a in w.more_menu.actions() if not a.isSeparator() and a.menu() is None]
        expected = [getattr(w, attr) for group in w.MORE_GROUPS for attr in group]
        self.assertEqual(menu_actions, expected)
        self.assertEqual(len(expected), 7)
        self.assertEqual(len(w.TOOLBAR_ACTIONS), 8)
        self.assertEqual(len([a for a in w.more_menu.actions() if a.menu() is not None]), 1)  # „Darstellung“
        self.assertFalse(set(w.TOOLBAR_ACTIONS) & {a for g in w.MORE_GROUPS for a in g})  # keine Aktion doppelt

    def test_shortcuts_are_registered_and_fire(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest

        w = self._window()
        expected = {"a_connect": "Return", "a_files": "F4", "a_new": "Ctrl+N", "a_new_group": "Ctrl+Shift+N", "a_edit": "F2",
                    "a_dup": "Ctrl+D", "a_del": "Del", "a_sync": "F5"}
        for attr, keys in expected.items():
            action = getattr(w, attr)
            self.assertIn(action, w.actions(), attr)
            self.assertEqual(action.shortcut().toString(), keys, attr)
        # Ein echter Tastendruck löst die Aktion aus (Duplizieren ohne Auswahl tut nichts, öffnet also keinen Dialog).
        fired = []
        w.a_dup.triggered.connect(lambda *_: fired.append(1))
        w.activateWindow()
        w.tree.setFocus()
        self.app.processEvents()
        QTest.keyClick(w, Qt.Key.Key_D, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(fired, [1])

    def test_buttons_trigger_actions_and_mirror_their_state(self):
        w = self._window()
        fired = []
        w.a_dup.triggered.connect(lambda *_: fired.append(1))
        button = w.tool_buttons["a_dup"]
        button.click()
        self.assertEqual(fired, [1])
        self.assertIn("Duplizieren", button.toolTip())
        self.assertIn("D", button.toolTip())  # Tastenkürzel steht im Tooltip
        w.a_dup.setEnabled(False)
        self.assertFalse(button.isEnabled())
        w.a_dup.setEnabled(True)
        self.assertTrue(button.isEnabled())
        w.a_dup.setText("Kopieren")
        self.assertEqual(button.text(), "Kopieren")
        for attr, btn in w.tool_buttons.items():
            self.assertFalse(btn.icon().isNull(), attr)
        self.assertTrue(w.tool_buttons["a_connect"].property("primary"))
        self.assertFalse(w.tool_buttons["a_new"].property("primary"))

    def test_button_icons_follow_theme(self):
        from sesam.gui import theme

        w = self._window()
        theme.apply(self.app, "light")
        light = w.tool_buttons["a_new"].icon().pixmap(18, 18).toImage().pixelColor(9, 5).name()
        theme.apply(self.app, "dark")
        dark = w.tool_buttons["a_new"].icon().pixmap(18, 18).toImage().pixelColor(9, 5).name()
        theme.apply(self.app, "system")
        self.assertNotEqual(light, dark)

    def _tree_ids(self, w):
        from PySide6.QtWidgets import QTreeWidgetItemIterator

        from sesam.gui.main_window import ID_ROLE

        out, it = [], QTreeWidgetItemIterator(w.tree)
        while it.value():
            out.append(it.value().data(0, ID_ROLE))
            it += 1
        return [i for i in out if i]

    def _window_with_connection(self, group="G"):
        from sesam.models import Connection

        w = self._window()
        conn = Connection("web-01", "h.example", user="u", group=group)
        w.cfg.connections.append(conn)
        w.cfg.groups = [group] if group else []
        w.refresh()
        self.assertTrue(w._select_connection(conn.id))
        return w, conn

    def test_duplicate_opens_edit_dialog_for_the_copy(self):
        from sesam import storage
        from sesam.gui.connection_dialog import ConnectionDialog

        w, conn = self._window_with_connection()
        seen = {}

        def fake_exec(dlg):
            seen["name"] = dlg.name.text()
            seen["selected"] = dlg.name.selectedText()
            dlg.name.setText("web-02")  # der Nutzer vergibt einen neuen Namen
            dlg._accept()
            return 1

        with mock.patch.object(ConnectionDialog, "exec", fake_exec):
            w.duplicate_selected()
        self.assertEqual(seen["name"], "web-01 (Kopie)")
        self.assertEqual(seen["selected"], "web-01 (Kopie)")  # Name ist markiert und sofort überschreibbar
        names = sorted(c.name for c in w.cfg.connections)
        self.assertEqual(names, ["web-01", "web-02"])  # Original unverändert, Kopie umbenannt
        self.assertEqual(sorted(c.name for c in storage.load(self.path).connections), ["web-01", "web-02"])  # gespeichert
        copy = next(c for c in w.cfg.connections if c.id != conn.id)
        self.assertEqual((copy.host, copy.user, copy.group), (conn.host, conn.user, conn.group))
        self.assertEqual(w._selected().id, copy.id)  # die Kopie ist ausgewählt

    def test_duplicate_cancel_creates_no_copy(self):
        from sesam.gui.connection_dialog import ConnectionDialog

        w, conn = self._window_with_connection()
        calls = []
        with mock.patch.object(ConnectionDialog, "exec", lambda dlg: calls.append(dlg) or 0):
            w.duplicate_selected()
        self.assertEqual(len(calls), 1)
        self.assertEqual([c.name for c in w.cfg.connections], ["web-01"])  # Abbrechen legt nichts an
        self.assertEqual(w._selected().name, "web-01")

    def test_duplicate_expands_collapsed_group_and_needs_selection(self):
        from sesam.gui.connection_dialog import ConnectionDialog

        w, conn = self._window_with_connection()
        w.tree.topLevelItem(0).setExpanded(False)  # Gruppe zugeklappt
        with mock.patch.object(ConnectionDialog, "exec", lambda dlg: dlg._accept() or 1):
            w.duplicate_selected()
        self.assertTrue(w.tree.topLevelItem(0).isExpanded())
        self.assertEqual(len(self._tree_ids(w)), 2)
        # ohne Auswahl passiert nichts
        w.tree.setCurrentItem(None)
        calls = []
        with mock.patch.object(ConnectionDialog, "exec", lambda dlg: calls.append(dlg) or 0):
            w.duplicate_selected()
        self.assertEqual(calls, [])
        self.assertEqual(len(w.cfg.connections), 2)

    def test_search_has_own_clear_button(self):
        w = self._window()
        self.assertFalse(w._clear_action.isVisible())
        w.search.setText("abc")
        self.assertTrue(w._clear_action.isVisible())
        w._clear_action.trigger()
        self.assertEqual(w.search.text(), "")
        self.assertFalse(w._clear_action.isVisible())


if __name__ == "__main__":
    unittest.main()
