"""Tests für den SFTP-Dateibrowser (Fenster) gegen einen lokalen SSH/SFTP-Server."""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))

from gui_base import GuiTestCase

try:
    import paramiko
except ImportError:  # paramiko nicht installiert
    paramiko = None

from sesam.models import Config, Connection


@unittest.skipIf(paramiko is None, "paramiko nicht verfügbar")
class SftpWindowTests(GuiTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if paramiko is not None:
            cls.host_key = paramiko.RSAKey.generate(2048)

    def setUp(self):
        super().setUp()
        from sesam import passwords, sftp
        from sftp_server import SftpTestServer

        base = Path(self.tmp.name)
        self.remote_root, self.local_dir = base / "remote", base / "local"
        self.remote_root.mkdir()
        self.local_dir.mkdir()
        self.server = SftpTestServer(str(self.remote_root), host_key=self.host_key)
        self.addCleanup(self.server.close)
        self.cfg = Config()
        self.conn = Connection(name="Test", host="127.0.0.1", port=self.server.port, user="tester", use_password=True)
        for target, name, fake in ((passwords, "get_password", lambda _i: "geheim"), (sftp, "default_known_hosts", lambda: base / "known_hosts")):
            patcher = mock.patch.object(target, name, fake)
            patcher.start()
            self.addCleanup(patcher.stop)

    # --- Hilfen ---
    def wait(self, condition, timeout=8.0):
        """Wartet auf einen Zustand. Bewusst processEvents + sleep statt QTest.qWait: Letzteres hielt den Interpreter-Lock und bremste die Netzwerk-Threads."""
        import time

        deadline = time.monotonic() + timeout
        while not condition() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.005)
        self.assertTrue(condition(), "Bedingung wurde nicht rechtzeitig erfüllt")

    def window(self, conn=None):
        from sesam.gui.sftp_window import SftpWindow

        w = SftpWindow(self.cfg, conn or self.conn)
        self.addCleanup(w.close)
        w.local_go(str(self.local_dir))
        return w

    def connected_window(self):
        w = self.window()
        self.wait(lambda: w.session is not None and w.remote.list.directory == "/")
        return w

    @staticmethod
    def select(panel, *names):
        panel.list.clearSelection()
        for i in range(panel.list.topLevelItemCount()):
            item = panel.list.topLevelItem(i)
            item.setSelected(item.name in names)

    def rows(self, w):
        return [w.transfers_layout.itemAt(i).widget() for i in range(w.transfers_layout.count()) if w.transfers_layout.itemAt(i).widget()]

    def wait_transfer_done(self, w, state="ok"):
        self.wait(lambda: self.rows(w) and all(r.finished_state for r in self.rows(w)))
        self.assertEqual([r.finished_state for r in self.rows(w)], [state] * len(self.rows(w)), [r.bar.format() for r in self.rows(w)])

    # --- Tests ---
    def test_connects_and_lists_remote_home(self):
        (self.remote_root / "datei.txt").write_text("hallo")
        (self.remote_root / "ordner").mkdir()
        w = self.window()
        self.assertFalse(w.remote.isEnabled())  # bis zur Verbindung gesperrt
        self.wait(lambda: w.remote.list.topLevelItemCount() == 2)
        self.assertTrue(w.remote.isEnabled())
        self.assertTrue(w.upload_button.isEnabled() and w.download_button.isEnabled())
        self.assertEqual(w.remote.path.text(), "/")
        self.assertEqual([w.remote.list.topLevelItem(i).name for i in range(2)], ["ordner", "datei.txt"])  # Ordner zuerst

    def test_navigate_into_folder_and_up(self):
        (self.remote_root / "sub").mkdir()
        (self.remote_root / "sub" / "x.txt").write_text("x")
        w = self.connected_window()
        self.wait(lambda: w.remote.list.topLevelItemCount() == 1)
        w.remote.list.open_requested.emit("sub")
        self.wait(lambda: w.remote.list.directory == "/sub")
        self.wait(lambda: w.remote.list.topLevelItemCount() == 1 and w.remote.list.topLevelItem(0).name == "x.txt")
        w.remote.up_button.click()
        self.wait(lambda: w.remote.list.directory == "/")

    def test_upload_selected_file(self):
        (self.local_dir / "datei.txt").write_text("inhalt")
        w = self.connected_window()
        self.wait(lambda: w.local.list.topLevelItemCount() == 1)
        self.select(w.local, "datei.txt")
        w.upload_button.click()
        self.wait_transfer_done(w)
        self.assertEqual((self.remote_root / "datei.txt").read_text(), "inhalt")
        self.wait(lambda: "datei.txt" in w.remote.list.entry_names())  # Liste wurde aktualisiert

    def test_download_selected_folder(self):
        (self.remote_root / "projekt" / "sub").mkdir(parents=True)
        (self.remote_root / "projekt" / "sub" / "b.txt").write_text("b")
        w = self.connected_window()
        self.wait(lambda: w.remote.list.topLevelItemCount() == 1)
        self.select(w.remote, "projekt")
        w.download_button.click()
        self.wait_transfer_done(w)
        self.assertEqual((self.local_dir / "projekt" / "sub" / "b.txt").read_text(), "b")
        self.wait(lambda: "projekt" in w.local.list.entry_names())

    def test_drop_from_local_into_subfolder(self):
        from PySide6.QtCore import QMimeData, QPointF, Qt
        from PySide6.QtGui import QDropEvent
        import json
        from sesam.gui.sftp_window import MIME

        (self.remote_root / "ziel").mkdir()
        (self.local_dir / "a.txt").write_text("a")
        w = self.connected_window()
        self.wait(lambda: w.remote.list.topLevelItemCount() == 1)
        mime = QMimeData()
        mime.setData(MIME, json.dumps({"side": "local", "dir": str(self.local_dir), "names": ["a.txt"]}).encode())
        item = w.remote.list.topLevelItem(0)
        pos = QPointF(w.remote.list.visualItemRect(item).center())
        event = QDropEvent(pos, Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        w.remote.list.dropEvent(event)
        self.wait_transfer_done(w)
        self.assertEqual((self.remote_root / "ziel" / "a.txt").read_text(), "a")

    def test_list_rejects_drops_from_its_own_side_and_unknown_data(self):
        from PySide6.QtCore import QMimeData
        import json
        from sesam.gui.sftp_window import MIME

        w = self.window()
        own, other, junk = QMimeData(), QMimeData(), QMimeData()
        own.setData(MIME, json.dumps({"side": "remote", "dir": "/", "names": ["x"]}).encode())
        other.setData(MIME, json.dumps({"side": "local", "dir": "/", "names": ["x"]}).encode())
        junk.setData(MIME, b"kein json")
        self.assertFalse(w.remote.list._acceptable(own))
        self.assertTrue(w.remote.list._acceptable(other))
        self.assertFalse(w.remote.list._acceptable(junk))
        self.assertFalse(w.local.list._acceptable(other))  # lokal nimmt nur Entferntes an
        self.assertFalse(w.local.list._acceptable(QMimeData()))

    def test_conflict_dialog_choices(self):
        (self.remote_root / "datei.txt").write_text("alt")
        (self.local_dir / "datei.txt").write_text("neu")
        w = self.connected_window()
        self.wait(lambda: w.remote.list.topLevelItemCount() == 1 and w.local.list.topLevelItemCount() == 1)
        self.select(w.local, "datei.txt")

        asked = []
        with mock.patch.object(type(w), "_ask_conflict", lambda self_, names: asked.append(names) or None):  # Abbrechen
            w.upload_selected()
            self.wait(lambda: asked)
            self.app.processEvents()
        self.assertEqual(asked, [["datei.txt"]])
        self.assertEqual(self.rows(w), [])
        self.assertEqual((self.remote_root / "datei.txt").read_text(), "alt")

        with mock.patch.object(type(w), "_ask_conflict", lambda self_, names: "skip"):
            w.upload_selected()
            self.wait(lambda: "Alles übersprungen" in w.statusBar().currentMessage())
        self.assertEqual((self.remote_root / "datei.txt").read_text(), "alt")
        self.assertEqual(self.rows(w), [])

        with mock.patch.object(type(w), "_ask_conflict", lambda self_, names: "overwrite"):
            w.upload_selected()
            self.wait(lambda: self.rows(w))
        self.wait_transfer_done(w)
        self.assertEqual((self.remote_root / "datei.txt").read_text(), "neu")

    def test_remote_new_folder_rename_delete(self):
        (self.remote_root / "alt.txt").write_text("x")
        w = self.connected_window()
        self.wait(lambda: w.remote.list.topLevelItemCount() == 1)

        with mock.patch.object(type(w), "_ask_name", lambda self_, *a, **k: "neu"):
            w.remote_new_folder()
            self.wait(lambda: (self.remote_root / "neu").is_dir() and "neu" in w.remote.list.entry_names())

        self.select(w.remote, "alt.txt")
        with mock.patch.object(type(w), "_ask_name", lambda self_, *a, **k: "umbenannt.txt"):
            w.remote_rename()
            self.wait(lambda: (self.remote_root / "umbenannt.txt").exists() and "umbenannt.txt" in w.remote.list.entry_names())
        self.assertFalse((self.remote_root / "alt.txt").exists())

        self.select(w.remote, "umbenannt.txt", "neu")
        with mock.patch.object(type(w), "_confirm_delete", lambda self_, names: False):
            w.remote_delete()
            self.app.processEvents()
        self.assertTrue((self.remote_root / "neu").exists())  # nicht bestätigt: nichts gelöscht
        with mock.patch.object(type(w), "_confirm_delete", lambda self_, names: True):
            w.remote_delete()
            self.wait(lambda: not (self.remote_root / "neu").exists() and not (self.remote_root / "umbenannt.txt").exists())
        self.wait(lambda: w.remote.list.topLevelItemCount() == 0)

    def test_name_with_path_separator_is_refused(self):
        from PySide6.QtWidgets import QInputDialog, QMessageBox

        w = self.window()
        warned = []
        with mock.patch.object(QInputDialog, "getText", lambda *a, **k: ("../x", True)), \
                mock.patch.object(QMessageBox, "warning", lambda *a, **k: warned.append(a)):
            self.assertIsNone(w._ask_name("Titel", "Name:"))
        self.assertEqual(len(warned), 1)

    def test_password_is_asked_once_when_none_is_saved(self):
        from sesam.gui.sftp_window import SftpWindow

        conn = Connection(name="Test", host="127.0.0.1", port=self.server.port, user="tester")  # kein gespeichertes Passwort
        with mock.patch.object(SftpWindow, "_ask_password", return_value="geheim") as ask:
            w = self.window(conn)
            self.wait(lambda: w.session is not None)
        ask.assert_called_once()

    def test_wrong_password_closes_window_after_one_prompt(self):
        from PySide6.QtWidgets import QMessageBox
        from sesam.gui.sftp_window import SftpWindow

        conn = Connection(name="Test", host="127.0.0.1", port=self.server.port, user="tester")
        shown = []
        with mock.patch.object(SftpWindow, "_ask_password", return_value="falsch") as ask, \
                mock.patch.object(QMessageBox, "critical", lambda *a, **k: shown.append(a[2])):
            w = self.window(conn)
            self.wait(lambda: shown)
        ask.assert_called_once()
        self.assertIn("Anmeldung abgelehnt", shown[0])
        self.assertIsNone(w.session)
        self.assertFalse(w.isVisible())

    def test_cancelled_password_prompt_shows_error_and_closes(self):
        from PySide6.QtWidgets import QMessageBox
        from sesam.gui.sftp_window import SftpWindow

        conn = Connection(name="Test", host="127.0.0.1", port=self.server.port, user="tester")
        shown = []
        with mock.patch.object(SftpWindow, "_ask_password", return_value=None), \
                mock.patch.object(QMessageBox, "critical", lambda *a, **k: shown.append(a[2])):
            self.window(conn)
            self.wait(lambda: shown)
        self.assertEqual(len(shown), 1)

    def test_jump_host_connection_shows_clear_error(self):
        from PySide6.QtWidgets import QMessageBox

        conn = Connection(name="Test", host="127.0.0.1", port=self.server.port, user="tester", jump_host="a@b", use_password=True)
        shown = []
        with mock.patch.object(QMessageBox, "critical", lambda *a, **k: shown.append(a[2])):
            self.window(conn)
            self.wait(lambda: shown)
        self.assertIn("Jump-Host", shown[0])

    def test_close_with_running_transfer_asks_and_cancels(self):
        (self.local_dir / "gross.bin").write_bytes(os.urandom(4 * 1024 * 1024))
        w = self.connected_window()
        self.wait(lambda: w.local.list.topLevelItemCount() == 1)
        self.select(w.local, "gross.bin")
        w.upload_button.click()
        self.wait(lambda: self.rows(w))
        with mock.patch.object(type(w), "_confirm_close", lambda self_: False):
            w.close()
        self.assertIsNotNone(w.session)  # Abbruch des Schließens: alles bleibt
        with mock.patch.object(type(w), "_confirm_close", lambda self_: True):
            w.close()
        self.assertIsNone(w.session)

    def test_clear_finished_removes_only_finished_rows(self):
        (self.local_dir / "a.txt").write_text("a")
        w = self.connected_window()
        self.wait(lambda: w.local.list.topLevelItemCount() == 1)
        self.select(w.local, "a.txt")
        w.upload_button.click()
        self.wait_transfer_done(w)
        self.assertEqual(len(self.rows(w)), 1)
        w.clear_finished()
        self.app.processEvents()
        from PySide6.QtCore import QCoreApplication, QEvent
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.assertEqual(self.rows(w), [])

    def test_sort_keeps_folders_on_top_in_both_directions(self):
        from PySide6.QtCore import Qt

        (self.remote_root / "b.txt").write_text("12345")
        (self.remote_root / "a.txt").write_text("1")
        (self.remote_root / "z-ordner").mkdir()
        w = self.connected_window()
        self.wait(lambda: w.remote.list.topLevelItemCount() == 3)
        for column in (0, 1):
            for order in (Qt.SortOrder.AscendingOrder, Qt.SortOrder.DescendingOrder):
                w.remote.list.sortByColumn(column, order)
                names = [w.remote.list.topLevelItem(i).name for i in range(3)]
                self.assertEqual(names[0], "z-ordner", (column, order, names))
        w.remote.list.sortByColumn(1, Qt.SortOrder.AscendingOrder)
        self.assertEqual([w.remote.list.topLevelItem(i).name for i in range(1, 3)], ["a.txt", "b.txt"])  # nach Größe

    def test_format_size(self):
        from sesam.gui.sftp_window import format_size

        self.assertEqual(format_size(0), "0 B")
        self.assertEqual(format_size(1023), "1023 B")
        self.assertEqual(format_size(1536), "1,5 KB")
        self.assertEqual(format_size(5 * 1024 * 1024), "5,0 MB")


@unittest.skipIf(paramiko is None, "paramiko nicht verfügbar")
class MainWindowIntegrationTests(GuiTestCase):
    def test_files_action_opens_browser_for_selected_connection(self):
        from sesam.gui import main_window
        from sesam.gui.main_window import MainWindow

        conn = Connection(name="Server", host="h.example", user="u")
        cfg = Config(connections=[conn])
        w = MainWindow(cfg, self.path)
        self.addCleanup(w.close)
        w.open_files()  # ohne Auswahl passiert nichts
        self.assertEqual(w._sftp_windows, [])
        self.assertTrue(w._select_connection(conn.id))
        with mock.patch.object(main_window, "SftpWindow") as window_class:
            w.open_files()
        window_class.assert_called_once_with(cfg, conn)
        window_class.return_value.show.assert_called_once()
        self.assertEqual(len(w._sftp_windows), 1)

    def test_files_action_is_in_sidebar_context_menu_and_has_shortcut(self):
        from sesam.gui.main_window import MainWindow

        w = MainWindow(Config(connections=[Connection(name="S", host="h")]), self.path)
        self.addCleanup(w.close)
        self.assertIn("a_files", w.side_buttons)
        self.assertEqual(w.a_files.shortcut().toString(), "F4")
        self.assertIn(w.a_files, w.actions())


if __name__ == "__main__":
    unittest.main()
