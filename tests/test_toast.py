"""Meldungen als Einblendung: Speicherung, Umschalten, Statuszeile bleibt für Version und WireGuard."""
import json
import tempfile
import unittest
from pathlib import Path

from gui_base import GuiTestCase

from sesam import storage
from sesam.models import Config, Connection


class StoredToastTests(unittest.TestCase):
    def test_default_off_and_tolerates_bad_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "c.json"
            storage.save(Config(), p)
            data = json.loads(p.read_text(encoding="utf-8"))
            self.assertIs(data["toasts"], False)
            for raw, expected in ((True, True), (False, False), ("ja", False), (1, False), (None, False)):
                data["toasts"] = raw
                p.write_text(json.dumps(data), encoding="utf-8")
                self.assertIs(storage.load(p).toasts, expected, raw)
            del data["toasts"]  # alte Konfiguration
            p.write_text(json.dumps(data), encoding="utf-8")
            self.assertIs(storage.load(p).toasts, False)


class ToastGuiTests(GuiTestCase):
    def setUp(self):
        super().setUp()
        storage.save(Config(connections=[Connection("a", "h")]), self.path)

    def _open(self):
        w = self._window()
        self.addCleanup(w.close)
        w.show()
        self.app.processEvents()
        return w

    def test_off_uses_the_status_bar(self):
        w = self._open()
        w.toasts.notify("Hallo", 5000)
        self.assertEqual(w.statusBar().currentMessage(), "Hallo")
        self.assertFalse(w.toasts._toast.isVisible())
        self.assertFalse(w.sync_label.isHidden())

    def test_on_shows_a_toast_and_keeps_version_in_the_status_bar(self):
        w = self._open()
        w.set_toasts(True)
        self.assertTrue(storage.load(self.path).toasts)  # wird gespeichert
        w.toasts.notify("Hallo", 5000)
        toast = w.toasts._toast
        self.assertTrue(toast.isVisible())
        self.assertEqual(toast.text(), "Hallo")
        self.assertEqual(w.statusBar().currentMessage(), "")
        self.assertFalse(w.version_label.isHidden())  # Version bleibt in der Statuszeile
        self.assertTrue(w.sync_label.isHidden())  # das Sync-Etikett wird durch die Einblendung ersetzt
        area = w.centralWidget().geometry()
        self.assertLessEqual(toast.geometry().right(), area.right())
        self.assertLessEqual(toast.geometry().bottom(), area.bottom())

    def test_sync_status_becomes_a_toast(self):
        w = self._open()
        w.set_toasts(True)
        w._sync_status("Synchronisiert 12:00 – alles aktuell", False)
        self.assertTrue(w.toasts._toast.isVisible())
        w.set_toasts(False)
        self.assertFalse(w.toasts._toast.isVisible())
        self.assertFalse(w.sync_label.isHidden())
        self.assertIn("Synchronisiert", w.sync_label.text())

    def test_click_closes_and_progress_text_stays(self):
        w = self._open()
        w.set_toasts(True)
        w.toasts.notify("Verbinde …")
        self.assertTrue(w.toasts._toast.isVisible())
        self.assertFalse(w.toasts._timer.isActive())  # läuft noch: bleibt bis zur nächsten Meldung
        w.toasts.notify("Fertig")
        self.assertTrue(w.toasts._timer.isActive())
        w.toasts._toast.mousePressEvent(None)
        self.assertFalse(w.toasts._toast.isVisible())

    def test_toast_text_is_plain(self):
        from PySide6.QtCore import Qt

        w = self._open()
        w.set_toasts(True)
        w.toasts.notify("<img src='x'> Container")
        self.assertEqual(w.toasts._toast.textFormat(), Qt.TextFormat.PlainText)

    def test_settings_dialog_toggle(self):
        from sesam.gui.settings_dialog import SettingsDialog

        seen = []
        dlg = SettingsDialog(100, "de", lambda v: None, lambda c: None, toasts=False, set_toasts=seen.append)
        self.addCleanup(dlg.close)
        self.assertFalse(dlg.toasts_box.isChecked())
        dlg.toasts_box.setChecked(True)
        self.assertEqual(seen, [True])


if __name__ == "__main__":
    unittest.main()
