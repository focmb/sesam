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
class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from ssh_verwaltung import passwords

        self.store = {}
        patches = [
            mock.patch.object(passwords, "set_password", lambda i, p: self.store.__setitem__(i, p)),
            mock.patch.object(passwords, "get_password", lambda i: self.store.get(i)),
            mock.patch.object(passwords, "delete_password", lambda i: self.store.pop(i, None)),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "c.json"

    def _window(self):
        from ssh_verwaltung import storage
        from ssh_verwaltung.gui.main_window import MainWindow

        return MainWindow(storage.load(self.path), self.path)

    def test_password_lifecycle(self):
        from ssh_verwaltung import storage
        from ssh_verwaltung.gui.connection_dialog import ConnectionDialog

        w = self._window()
        dlg = ConnectionDialog(w.cfg)
        dlg.name.setText("S")
        dlg.host.setText("h")
        dlg.password.setText("pw1")
        dlg._accept()
        self.assertEqual(dlg.password_change, "pw1")
        conn = dlg.result_connection()
        w._apply_password(conn, dlg.password_change)
        w.cfg.connections.append(conn)
        w._save()
        w.refresh()
        self.assertTrue(conn.use_password)
        self.assertEqual(self.store[conn.id], "pw1")
        self.assertNotIn("pw1", self.path.read_text())  # nie in der Konfigurationsdatei

        w.tree.setCurrentItem(w.tree.topLevelItem(0))
        w.duplicate_selected()
        self.assertEqual(len(w.cfg.connections), 2)
        self.assertEqual(self.store[w.cfg.connections[1].id], "pw1")

        d2 = ConnectionDialog(w.cfg, conn)
        d2.clear_password.setChecked(True)
        d2._accept()
        self.assertEqual(d2.password_change, "")
        w._apply_password(conn, d2.password_change)
        self.assertFalse(conn.use_password)
        self.assertNotIn(conn.id, self.store)
        w._save()
        self.assertEqual([c.use_password for c in storage.load(self.path).connections], [False, True])

    def test_custom_terminal_and_templates(self):
        from ssh_verwaltung import storage
        from ssh_verwaltung.gui.terminals_dialog import TerminalsDialog

        cfg = storage.load(self.path)
        td = TerminalsDialog(cfg)
        td._add()
        self.assertEqual(td.terminals[-1].command, "")
        td.name.setText("Mein Ghostty")
        td.command.setText("/opt/my ghostty/ghostty -e {ssh_args}")
        td._store()
        self.assertEqual(td.terminals[-1].name, "Mein Ghostty")
        terms, default = td.result_values()
        self.assertTrue(default)
        self.assertIn("/opt/my ghostty/ghostty", terms[-1].command)


if __name__ == "__main__":
    unittest.main()
