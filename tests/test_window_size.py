"""Fenstergröße wird beim Beenden gespeichert und beim Start wiederhergestellt."""
import json
import unittest

from gui_base import GuiTestCase

from sesam import storage
from sesam.models import Config, Connection


class StorageTests(unittest.TestCase):
    def test_invalid_values_are_ignored(self):
        for bad in ("x", [1], [800, 600, 1], [800, "a"], [10, 10], [800, 999999], [True, True], None, {"a": 1}):
            self.assertEqual(storage._from_data({"window_size": bad}).window_size, [], bad)
        self.assertEqual(storage._from_data({"window_size": [1200, 700.0]}).window_size, [1200, 700])
        self.assertFalse(storage._from_data({"window_maximized": "ja"}).window_maximized)
        self.assertTrue(storage._from_data({"window_maximized": True}).window_maximized)

    def test_roundtrip(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.json"
            storage.save(Config(window_size=[1111, 777], window_maximized=True), path)
            cfg = storage.load(path)
            self.assertEqual((cfg.window_size, cfg.window_maximized), ([1111, 777], True))
            self.assertIn("window_size", json.loads(path.read_text()))


class WindowTests(GuiTestCase):
    def expect(self, w, width, height):
        """Erwartete Größe: Wunsch, aber nie kleiner als das Mindestmaß des Fensters und nie größer als der Bildschirm."""
        hint, avail = w.minimumSizeHint(), w.screen().availableGeometry()
        return (max(min(width, avail.width()), hint.width()), max(min(height, avail.height()), hint.height()))

    def _window(self):  # wie beim echten Start: keine feste Größe von außen
        from sesam.gui.main_window import MainWindow

        w = MainWindow(storage.load(self.path), self.path)
        w.show()
        self.app.processEvents()
        return w

    def open(self, **kw):
        storage.save(Config(connections=[Connection("a", "h")], **kw), self.path)
        w = self._window()
        self.addCleanup(w.close)
        return w

    def test_default_size_without_saved_value(self):
        w = self.open()
        self.assertEqual((w.width(), w.height()), self.expect(w, 1000, 600))

    def test_restores_saved_size(self):
        w = self.open(window_size=[780, 560])
        self.assertEqual((w.width(), w.height()), self.expect(w, 780, 560))

    def test_close_saves_size_and_next_start_uses_it(self):
        w = self.open()
        w.resize(780, 640)
        self.app.processEvents()
        size = [w.width(), w.height()]
        w.close()
        cfg = storage.load(self.path)
        self.assertEqual((cfg.window_size, cfg.window_maximized), (size, False))
        self.assertEqual(len(cfg.connections), 1)  # übrige Daten bleiben erhalten
        w2 = self._window()
        self.addCleanup(w2.close)
        self.assertEqual((w2.width(), w2.height()), self.expect(w2, *size))

    def test_oversized_value_is_limited_to_screen(self):
        w = self.open(window_size=[19000, 19000])
        avail = w.screen().availableGeometry()
        self.assertLessEqual(w.width(), avail.width())
        self.assertLessEqual(w.height(), avail.height())

    def test_maximized_state_is_remembered_with_normal_size(self):
        w = self.open(window_size=[780, 560], window_maximized=True)
        self.app.processEvents()
        self.assertTrue(w.isMaximized() or w.cfg.window_maximized)
        w.cfg.window_size = [780, 560]
        w.showMaximized()
        self.app.processEvents()
        w.close()
        cfg = storage.load(self.path)
        self.assertTrue(cfg.window_maximized)
        self.assertEqual(cfg.window_size, [780, 560])  # die normale Größe, nicht die maximierte


if __name__ == "__main__":
    unittest.main()
