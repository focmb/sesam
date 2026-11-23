"""Werkzeugzeile ohne Menüleiste: Farbschema im Menü „Weitere Aktionen“, Einstellungen ganz rechts."""
import unittest
from unittest import mock

from gui_base import GuiTestCase

from sesam import storage
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

    def test_settings_button_is_the_last_one_in_the_row(self):
        w = self.w
        self.assertEqual(w.settings_button.text(), "Einstellungen")
        self.assertFalse(w.settings_button.icon().isNull())
        self.assertGreater(w.settings_button.x(), w.more_button.x())
        self.assertGreater(w.more_button.x(), w.tool_buttons["a_fav"].x())
        self.assertIsNotNone(w.more_button.menu())
        self.assertTrue(w.more_button.toolTip())

    def test_three_theme_entries_in_the_appearance_menu(self):
        w = self.w
        self.assertEqual(list(w.theme_actions), ["light", "dark", "system"])
        look = next(a.menu() for a in w.more_menu.actions() if a.menu() is not None)
        self.assertEqual(look.actions(), list(w.theme_actions.values()))
        for mode, action in w.theme_actions.items():
            self.assertTrue(action.text())
            self.assertTrue(action.isCheckable())

    def test_exactly_one_entry_is_checked_and_follows_config(self):
        w = self.w
        checked = lambda: [m for m, a in w.theme_actions.items() if a.isChecked()]
        self.assertEqual(checked(), [w.cfg.theme])
        w.theme_actions["dark"].trigger()
        self.assertEqual(w.cfg.theme, "dark")
        self.assertEqual(checked(), ["dark"])
        w.theme_actions["light"].trigger()
        self.assertEqual((w.cfg.theme, w.theme_actions["dark"].isChecked()), ("light", False))
        w.theme_actions["system"].trigger()
        self.assertEqual(w.cfg.theme, "system")
        self.assertEqual(storage.load(self.path).theme, "system")  # wird gespeichert

    def test_check_follows_a_theme_change_made_elsewhere(self):
        w = self.w
        w.cfg.theme = "dark"  # z. B. im Einstellungsdialog
        w._refresh_icons()
        self.assertEqual([m for m, a in w.theme_actions.items() if a.isChecked()], ["dark"])
        w.theme_actions["system"].trigger()

    def test_settings_button_opens_the_settings_dialog(self):
        from sesam.gui.settings_dialog import SettingsDialog

        opened = []
        with mock.patch.object(SettingsDialog, "exec", lambda dlg: opened.append((dlg.windowTitle(), dlg.size_box.currentData(), dlg.language_box.currentData()))):
            self.w.settings_button.click()
        self.assertEqual(opened, [("Einstellungen", 100, "system")])

    def test_font_shortcuts_work_without_menu(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest

        w = self.w
        w.set_font_scale(150)
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
        in_toolbar = [*w.TOOLBAR_ACTIONS, *(a for g in w.MORE_GROUPS for a in g)]
        for attr in ("a_sync_settings", "a_terms", "a_viewers"):
            self.assertNotIn(attr, in_toolbar)  # liegen jetzt in den Einstellungen
        self.assertEqual(w.a_sync_settings.text(), "Synchronisation …")
        from sesam.gui import main_window

        texts = []
        with mock.patch.object(main_window.MainWindow, "_popup", lambda self, menu, pos: texts.extend(a.text() for a in menu.actions())):
            w._context_menu(w.tree.viewport().rect().bottomRight())
        self.assertIn(w.a_import.text(), texts)

    def test_wider_font_keeps_the_row_inside_the_list_column(self):
        self.w.set_font_scale(200)
        for _ in range(5):
            self.app.processEvents()
        w = self.w
        size = w.tool_buttons["a_new"].iconSize().width()
        self.assertGreater(size, 18)  # Symbole wachsen mit der Schrift
        right = w.settings_button.mapTo(w, w.settings_button.rect().topRight()).x()
        self.assertLessEqual(right, w.splitter.widget(0).mapTo(w, w.splitter.widget(0).rect().topRight()).x())
        w.set_font_scale(100)


if __name__ == "__main__":
    unittest.main()
