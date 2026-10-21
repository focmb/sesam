"""Variable Schriftgröße: Menü, Tastenkürzel, Speicherung."""
import json
import unittest

from gui_base import GuiTestCase

from sesam import storage
from sesam.gui import theme
from sesam.models import Config, Connection


class StoredScaleTests(unittest.TestCase):
    def test_load_tolerates_bad_values(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "c.json"
            storage.save(Config(), p)
            data = json.loads(p.read_text(encoding="utf-8"))
            self.assertEqual(data["font_scale"], 100)
            for raw, expected in ((125, 125), (5, 70), (900, 200), ("groß", 100), (None, 100), (True, 100), (float("nan"), 100), (119.7, 119)):
                data["font_scale"] = raw
                p.write_text(json.dumps(data), encoding="utf-8")
                self.assertEqual(storage.load(p).font_scale, expected, raw)
            del data["font_scale"]  # alte Konfiguration
            p.write_text(json.dumps(data), encoding="utf-8")
            self.assertEqual(storage.load(p).font_scale, 100)

    def test_next_scale(self):
        self.assertEqual(theme.next_scale(100, +1), 110)
        self.assertEqual(theme.next_scale(100, -1), 90)
        self.assertEqual(theme.next_scale(200, +1), 200)
        self.assertEqual(theme.next_scale(80, -1), 80)
        self.assertEqual(theme.next_scale(105, +1), 110)  # krumme Werte rasten auf die nächste Stufe ein
        self.assertEqual(theme.next_scale(105, -1), 100)


class FontScaleTests(GuiTestCase):
    def setUp(self):
        super().setUp()
        self._orig = self.app.font()
        self.addCleanup(lambda: (theme.apply_scale(self.app, 100), self.app.setFont(self._orig)))
        theme.apply_scale(self.app, 100)
        storage.save(Config(connections=[Connection("a", "h")]), self.path)

    def _size(self):
        f = self.app.font()
        return f.pointSizeF() if f.pointSizeF() > 0 else f.pixelSize()

    def test_scale_changes_app_font_and_is_saved_without_sync(self):
        from unittest import mock

        w = self._window()
        base = self._size()
        with mock.patch.object(w.sync, "schedule") as schedule:
            w.set_font_scale(150)
        schedule.assert_not_called()
        self.assertAlmostEqual(self._size(), base * 1.5, delta=1)
        self.assertEqual(storage.load(self.path).font_scale, 150)
        self.assertTrue(w.scale_actions[150].isChecked())
        w.set_font_scale(100)
        self.assertAlmostEqual(self._size(), base, delta=0.01)  # zurück ohne Rundungsdrift
        self.assertTrue(w.scale_actions[100].isChecked())

    def test_bigger_smaller_reset_actions_and_limits(self):
        w = self._window()
        w.a_font_bigger.trigger()
        self.assertEqual(w.cfg.font_scale, 110)
        w.a_font_smaller.trigger()
        w.a_font_smaller.trigger()
        self.assertEqual(w.cfg.font_scale, 90)
        w.a_font_reset.trigger()
        self.assertEqual(w.cfg.font_scale, 100)
        for _ in range(20):
            w.a_font_bigger.trigger()
        self.assertEqual(w.cfg.font_scale, 200)
        for _ in range(20):
            w.a_font_smaller.trigger()
        self.assertEqual(w.cfg.font_scale, 80)
        w.set_font_scale(5000)  # unsinnige Werte werden begrenzt
        self.assertEqual(w.cfg.font_scale, 200)

    def test_sidebar_and_icons_grow_with_font(self):
        w = self._window()
        small = w.sidebar.width(), w.tree.iconSize().width()
        w.set_font_scale(200)
        self.app.processEvents()
        self.assertGreater(w.tree.iconSize().width(), small[1])
        wide = w.sidebar.maximumWidth()
        for button in w.side_buttons.values():  # nichts wird abgeschnitten
            self.assertLessEqual(button.sizeHint().width(), w.sidebar.maximumWidth())
        original = w.a_sync.text()
        w.a_sync.setText("Jetzt synchronisieren (ein sehr langer Beschriftungstext)")  # Textwechsel bleibt passend
        self.assertLessEqual(w.side_buttons["a_sync"].sizeHint().width(), w.sidebar.maximumWidth())
        w.a_sync.setText(original)
        w.set_font_scale(100)
        self.app.processEvents()
        self.assertLess(w.sidebar.maximumWidth(), wide)  # und schrumpft mit der Schrift wieder

    def test_shortcuts(self):
        from PySide6.QtGui import QKeySequence

        w = self._window()
        keys = lambda a: {k.toString() for k in a.shortcuts()}
        self.assertIn("Ctrl++", keys(w.a_font_bigger))
        self.assertIn("Ctrl+=", keys(w.a_font_bigger))  # auch ohne Umschalttaste erreichbar
        self.assertEqual(keys(w.a_font_smaller), {"Ctrl+-"})
        self.assertEqual(keys(w.a_font_reset), {"Ctrl+0"})
        self.assertNotIn(QKeySequence("Ctrl+0"), [a.shortcut() for a in (w.a_new, w.a_connect)])


if __name__ == "__main__":
    unittest.main()
