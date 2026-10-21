"""Seitenleiste ohne Menüleiste: Darstellung und Schriftgröße als Auswahlknöpfe unter einem Trennstrich."""
import unittest
from unittest import mock

from gui_base import GuiTestCase

from sesam import storage
from sesam.gui import theme
from sesam.models import Config


class SidebarViewTests(GuiTestCase):
    def setUp(self):
        super().setUp()
        storage.save(Config(), self.path)
        self.w = self._window()
        self.addCleanup(self.w.close)

    def test_version_stands_at_the_left_of_the_status_bar(self):
        from sesam import __version__

        w = self.w
        label = w.version_label
        self.assertEqual(label.text(), f"Version {__version__}")
        self.assertTrue(label.isVisible())
        bar = w.statusBar()
        self.assertIs(label.parent(), bar)
        self.assertLess(label.x(), w.sync_label.x())  # links, der Sync-Status bleibt rechts
        self.assertLess(label.mapTo(w, label.rect().topLeft()).x(), 40)
        self.assertGreater(label.mapTo(w, label.rect().topLeft()).y(), w.height() - 60)  # ganz unten

    def test_version_label_is_not_removed_by_sync_status(self):
        w = self.w
        w._sync_status("Synchronisiert", False)
        self.app.processEvents()
        self.assertTrue(w.version_label.isVisible())

    def test_menu_bar_is_gone(self):
        self.assertEqual(self.w.menuBar().actions(), [])

    def test_font_button_and_theme_switches_sit_below_a_separator_at_the_end(self):
        from PySide6.QtWidgets import QFrame

        w = self.w
        self.assertEqual(list(w.side_menu_buttons), ["font"])  # „Darstellung“ gibt es nicht mehr
        self.assertEqual(w.side_menu_buttons["font"].text(), "Schriftgröße")
        ys = [w.side_buttons["a_terms"].y(), w.side_menu_buttons["font"].y(), w.theme_row.y()]
        self.assertTrue(ys[0] < ys[1] < ys[2])
        separators = [f for f in w.sidebar.findChildren(QFrame) if f.objectName() == "sideSep"]
        self.assertEqual(len(separators), 2)
        self.assertEqual(len([s for s in separators if ys[0] < s.y() < ys[1]]), 1)
        self.assertEqual({b.width() for b in (*w.side_buttons.values(), *w.side_menu_buttons.values())}, {w.sidebar.width()})
        self.assertEqual(w.theme_row.width(), w.sidebar.width())

    def test_three_icon_only_theme_switches(self):
        w = self.w
        self.assertEqual(list(w.theme_buttons), ["light", "dark", "system"])
        for mode, b in w.theme_buttons.items():
            self.assertEqual(b.text(), "")
            self.assertFalse(b.icon().isNull())
            self.assertTrue(b.toolTip())
            self.assertTrue(b.isCheckable())
        widths = {b.width() for b in w.theme_buttons.values()}
        self.assertLessEqual(max(widths) - min(widths), 1)
        self.assertLess(max(widths), w.sidebar.width() / 2)  # schmal: drei passen locker nebeneinander

    def test_exactly_one_switch_is_checked_and_follows_config(self):
        w = self.w
        self.assertEqual([m for m, b in w.theme_buttons.items() if b.isChecked()], [w.cfg.theme])
        w.theme_buttons["dark"].click()
        self.assertEqual(w.cfg.theme, "dark")
        self.assertEqual([m for m, b in w.theme_buttons.items() if b.isChecked()], ["dark"])
        w.theme_buttons["light"].click()
        self.assertEqual((w.cfg.theme, w.theme_buttons["dark"].isChecked()), ("light", False))
        w.theme_buttons["system"].click()
        self.assertEqual(w.cfg.theme, "system")
        self.assertEqual(storage.load(self.path).theme, "system")  # wird gespeichert

    def test_clicking_the_checked_switch_keeps_it_selected(self):
        w = self.w
        w.theme_buttons["dark"].click()
        w.theme_buttons["dark"].click()
        self.assertTrue(w.theme_buttons["dark"].isChecked())
        self.assertEqual(w.cfg.theme, "dark")

    def test_active_switch_icon_uses_accent_text_colour(self):
        w = self.w
        w.theme_buttons["dark"].click()
        self.app.processEvents()
        c = theme.colors()

        def dominant(button):
            image = button.icon().pixmap(18, 18).toImage()
            counts = {}
            for y in range(image.height()):
                for x in range(image.width()):
                    px = image.pixelColor(x, y)
                    if px.alpha() > 100:
                        counts[px.name()] = counts.get(px.name(), 0) + 1
            return max(counts, key=counts.get)

        def close(a, b):
            return all(abs(int(a[i:i + 2], 16) - int(b[i:i + 2], 16)) <= 8 for i in (1, 3, 5))

        self.assertTrue(close(dominant(w.theme_buttons["dark"]), c["accent_text"]))
        self.assertTrue(close(dominant(w.theme_buttons["light"]), c["text"]))
        w.theme_buttons["system"].click()

    def test_font_button_opens_its_menu(self):
        from PySide6.QtWidgets import QMenu

        opened = []
        with mock.patch.object(QMenu, "popup", lambda menu, pos, *a: opened.append(menu.title())):
            self.w.side_menu_buttons["font"].click()
        self.assertEqual(opened, ["Schriftgröße"])

    def test_font_menu_entries_and_shortcuts_work_without_menu(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest

        w = self.w
        self.assertEqual(len(w.scale_actions), len(theme.SCALE_STEPS))
        w.scale_actions[150].trigger()
        self.assertEqual(w.cfg.font_scale, 150)
        w.a_font_reset.trigger()
        self.assertEqual(w.cfg.font_scale, 100)
        w.activateWindow()
        QTest.keyClick(w, Qt.Key.Key_Plus, Qt.KeyboardModifier.ControlModifier)
        self.app.processEvents()
        self.assertGreater(w.cfg.font_scale, 100)
        w.a_font_reset.trigger()
        self.assertIn(w.a_quit, w.actions())

    def test_everything_formerly_in_menus_is_still_reachable(self):
        w = self.w
        self.assertIn("a_sync_settings", [a for g in w.SIDEBAR_GROUPS for a in g])
        self.assertEqual(w.a_sync_settings.text(), "Synchronisation …")
        from sesam.gui import main_window

        texts = []
        with mock.patch.object(main_window.MainWindow, "_popup", lambda self, menu, pos: texts.extend(a.text() for a in menu.actions())):
            w._context_menu(w.tree.viewport().rect().bottomRight())
        self.assertIn(w.a_import.text(), texts)

    def test_wider_font_keeps_all_buttons_equal_and_visible(self):
        self.w.set_font_scale(200)
        for _ in range(5):
            self.app.processEvents()
        buttons = [*self.w.side_buttons.values(), *self.w.side_menu_buttons.values()]
        self.assertEqual({b.width() for b in buttons}, {self.w.sidebar.width()})
        self.assertEqual(self.w.theme_row.width(), self.w.sidebar.width())
        self.assertGreaterEqual(self.w.sidebar.width(), max(b.sizeHint().width() for b in buttons))
        self.w.set_font_scale(100)


if __name__ == "__main__":
    unittest.main()
