import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication
except ImportError:  # PySide6 oder Qt-Systembibliotheken fehlen
    QApplication = None


@unittest.skipIf(QApplication is None, "PySide6 nicht verfügbar")
class StyleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self):
        from sesam.gui import theme

        theme.apply(self.app, "system")  # globale Darstellung für andere Tests zurücksetzen

    def test_token_sets_are_complete_and_stylesheet_fully_resolved(self):
        from sesam.gui import style

        self.assertEqual(set(style.TOKENS["light"]), set(style.TOKENS["dark"]))
        for mode in ("light", "dark"):
            qss = style.stylesheet(style.tokens(mode))
            self.assertNotIn("@", qss, "Platzhalter im Stylesheet nicht ersetzt")
            self.assertIn(style.tokens(mode)["accent"], qss)

    def test_icons_render_visible_pixels(self):
        from sesam.gui import style

        for name in style._ICONS:
            icon = style.icon(name, "#ff0000")
            self.assertFalse(icon.isNull(), name)
            image = icon.pixmap(18, 18).toImage()
            visible = any(image.pixelColor(x, y).alpha() > 0 for x in range(image.width()) for y in range(image.height()))
            self.assertTrue(visible, f"Icon {name} ist leer")

    def test_arrow_files_are_written(self):
        from pathlib import Path

        from sesam.gui import style

        files = style._arrow_files(style.tokens("dark"))
        for key in ("arrow_right", "arrow_down", "arrow_up"):
            self.assertNotEqual(files[key], "none", key)
            self.assertTrue(Path(files[key]).exists(), key)

    def test_theme_switch_applies_colors_and_notifies(self):
        from sesam.gui import style, theme

        changed = []
        theme.notifier().changed.connect(lambda: changed.append(1))
        self.assertEqual(theme.apply(self.app, "dark"), "dark")
        self.assertEqual(theme.colors()["bg"], style.TOKENS["dark"]["bg"])
        self.assertIn(style.TOKENS["dark"]["surface"], self.app.styleSheet())
        self.assertEqual(theme.apply(self.app, "light"), "light")
        self.assertEqual(theme.colors()["bg"], style.TOKENS["light"]["bg"])
        self.assertEqual(len(changed), 2)

    def test_main_window_recolors_icons_on_theme_change(self):
        import tempfile
        from pathlib import Path

        from sesam.gui import theme
        from sesam.gui.main_window import MainWindow
        from sesam.models import Config, Connection

        with tempfile.TemporaryDirectory() as tmp:
            cfg = Config(connections=[Connection("a", "h.example", group="G")], groups=["G"])
            w = MainWindow(cfg, Path(tmp) / "c.json")
            theme.apply(self.app, "light")
            light = w.a_new.icon().pixmap(18, 18).toImage().pixelColor(9, 5)
            theme.apply(self.app, "dark")
            dark = w.a_new.icon().pixmap(18, 18).toImage().pixelColor(9, 5)
            self.assertNotEqual(light.name(), dark.name())  # Icon wurde neu eingefärbt
            self.assertFalse(w.a_connect.icon().isNull())
            self.assertEqual(w.tree.topLevelItemCount(), 1)  # Baum nach Wechsel neu aufgebaut
            w.close()
            w.deleteLater()


if __name__ == "__main__":
    unittest.main()
