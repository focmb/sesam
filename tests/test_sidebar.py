"""Seitenleiste am rechten Rand: Anordnung, Tastenkürzel, Zustand der Knöpfe, Suchfeld."""
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
        self.path = Path(self.tmp.name) / "c.json"

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

    def test_actions_stand_below_each_other_on_the_right(self):
        from PySide6.QtWidgets import QToolBar

        w = self._window()
        order = [attr for group in w.SIDEBAR_GROUPS for attr in group]
        self.assertEqual(len(order), 9)
        self.assertEqual(set(w.side_buttons), set(order))
        points = [self._pos(w.side_buttons[attr], w) for attr in order]
        ys = [p.y() for p in points]
        self.assertTrue(all(a < b for a, b in zip(ys, ys[1:])), f"nicht untereinander: {ys}")
        self.assertEqual(len({p.x() for p in points}), 1, "Knöpfe stehen nicht bündig")
        tree_right = w.tree.mapTo(w, w.tree.rect().topRight()).x()
        self.assertGreater(points[0].x(), tree_right)  # rechts vom Baum
        self.assertGreater(points[0].x(), w.width() // 2)
        self.assertEqual(w.findChildren(QToolBar), [])  # die alte Werkzeugleiste ist weg
        widths = {w.side_buttons[attr].width() for attr in order}
        self.assertEqual(len(widths), 1)  # alle gleich breit

    def test_shortcuts_are_registered_and_fire(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest

        w = self._window()
        expected = {"a_connect": "Return", "a_new": "Ctrl+N", "a_new_group": "Ctrl+Shift+N", "a_edit": "F2",
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
        button = w.side_buttons["a_dup"]
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
        for attr, btn in w.side_buttons.items():
            self.assertFalse(btn.icon().isNull(), attr)
        self.assertTrue(w.side_buttons["a_connect"].property("primary"))
        self.assertFalse(w.side_buttons["a_new"].property("primary"))

    def test_button_icons_follow_theme(self):
        from sesam.gui import theme

        w = self._window()
        theme.apply(self.app, "light")
        light = w.side_buttons["a_new"].icon().pixmap(18, 18).toImage().pixelColor(9, 5).name()
        theme.apply(self.app, "dark")
        dark = w.side_buttons["a_new"].icon().pixmap(18, 18).toImage().pixelColor(9, 5).name()
        theme.apply(self.app, "system")
        self.assertNotEqual(light, dark)

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
