"""VNC in der Oberfläche: Verbindungsdialog, Viewer-Verwaltung, Hauptfenster (Start, Markierung, Passwort, Löschen)."""
import sys
import unittest
from pathlib import Path
from unittest import mock

from gui_base import GuiTestCase

from sesam import launcher, passwords, storage, vnc
from sesam.models import Config, Connection, Viewer
from test_vnc import wait_for


class DialogTests(GuiTestCase):
    def dialog(self, conn=None, cfg=None):
        from sesam.gui.connection_dialog import ConnectionDialog

        d = ConnectionDialog(cfg or Config(viewers=[Viewer("A", "a"), Viewer("B", "b")]), conn)
        self.addCleanup(d.close)
        return d

    def test_vnc_fields_are_disabled_while_off(self):
        d = self.dialog()
        for w in (d.vnc_host, d.vnc_port, d.vnc_viewer, d.vnc_password):
            self.assertFalse(w.isEnabled())
        d.vnc_mode.setCurrentIndex(d.vnc_mode.findData("direct"))
        self.assertTrue(d.vnc_port.isEnabled() and d.vnc_password.isEnabled())
        self.assertFalse(d.vnc_host.isEnabled())  # „Ziel vom Server aus“ gibt es nur beim Tunnel
        d.vnc_mode.setCurrentIndex(d.vnc_mode.findData("tunnel"))
        self.assertTrue(d.vnc_host.isEnabled())

    def test_values_are_saved_and_password_change_reported(self):
        cfg = Config(viewers=[Viewer("A", "a"), Viewer("B", "b")])
        d = self.dialog(cfg=cfg)
        d.name.setText("PC")
        d.host.setText("srv")
        d.vnc_mode.setCurrentIndex(d.vnc_mode.findData("tunnel"))
        d.vnc_host.setText("gerät")
        d.vnc_port.setValue(5901)
        d.vnc_viewer.setCurrentIndex(d.vnc_viewer.findData(cfg.viewers[1].id))
        d.vnc_password.setText("geheim")
        d._accept()
        c = d.result_connection()
        self.assertEqual((c.vnc, c.vnc_host, c.vnc_port, c.vnc_viewer_id), ("tunnel", "gerät", 5901, cfg.viewers[1].id))
        self.assertEqual(d.vnc_password_change, "geheim")
        self.assertIsNone(d.password_change)  # das SSH-Passwort bleibt davon unberührt

    def test_existing_password_is_kept_cleared_or_replaced(self):
        conn = Connection("PC", "h", vnc="direct", vnc_use_password=True)
        d = self.dialog(conn)
        d._accept()
        self.assertIsNone(d.vnc_password_change)
        d = self.dialog(conn)
        d.vnc_clear_password.setChecked(True)
        d._accept()
        self.assertEqual(d.vnc_password_change, "")

    def test_host_only_kept_for_tunnel_mode(self):
        conn = Connection("PC", "h", vnc="tunnel", vnc_host="x")
        d = self.dialog(conn)
        d.vnc_mode.setCurrentIndex(d.vnc_mode.findData("direct"))
        d._accept()
        self.assertEqual(conn.vnc_host, "")


class ViewersDialogTests(GuiTestCase):
    def dialog(self, cfg):
        from sesam.gui.viewers_dialog import ViewersDialog

        d = ViewersDialog(cfg)
        self.addCleanup(d.close)
        return d

    def test_edit_add_default_and_remove(self):
        a, b = Viewer("A", "a {host}"), Viewer("B", "b {host}")
        d = self.dialog(Config(viewers=[a, b], default_viewer_id=a.id))
        self.assertEqual(d.list.count(), 2)
        d.list.setCurrentRow(1)
        d.command.setText("bb {host}")
        d.command.textEdited.emit("bb {host}")
        d._make_default()
        viewers, default = d.result_values()
        self.assertEqual((viewers[1].command, default), ("bb {host}", b.id))
        d._add()
        self.assertEqual(d.list.count(), 3)
        d.list.setCurrentRow(1)
        d._remove()
        viewers, default = d.result_values()
        self.assertEqual([v.name for v in viewers], ["A", tr_own()])
        self.assertEqual(default, a.id)  # der gelöschte Standard-Viewer wird ersetzt

    def test_original_config_is_not_changed_by_cancel(self):
        cfg = Config(viewers=[Viewer("A", "a")])
        d = self.dialog(cfg)
        d.name.setText("X")
        d.name.textEdited.emit("X")
        self.assertEqual(cfg.viewers[0].name, "A")


def tr_own():
    from sesam.i18n import tr

    return tr("Eigener Viewer")


class WindowTests(GuiTestCase):
    def setUp(self):
        super().setUp()
        script = Path(self.tmp.name) / "viewer.py"
        script.write_text("import time\ntime.sleep(60)\n", encoding="utf-8")
        self.viewer = Viewer("fake", f'"{sys.executable}" "{script}" {{host}} {{port}}')
        self.conn = Connection("Gerät", "10.1.2.3", vnc="direct", vnc_use_password=True)
        self.plain = Connection("Server", "srv")
        storage.save(Config(connections=[self.conn, self.plain], viewers=[self.viewer], default_viewer_id=self.viewer.id), self.path)
        self.w = self._window()
        self.addCleanup(self.w.vnc.stop_all)
        for name, fake in (("get_password", lambda i: "geheim"), ("set_password", lambda i, p: self.stored.__setitem__(i, p)),
                           ("delete_password", lambda i: self.stored.pop(i, None))):
            patcher = mock.patch.object(passwords, name, fake)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.stored: dict = {}

    def marker(self, conn):
        from sesam.gui.main_window import OPEN_ROLE

        return bool(self.w._find_item(conn.id).data(0, OPEN_ROLE))

    def test_open_vnc_shows_green_dot_and_close_removes_it(self):
        self.assertFalse(self.marker(self.conn))
        self.w.open_vnc(self.conn)
        self.assertTrue(self.w.vnc.is_open(self.conn.id))
        self.assertTrue(self.marker(self.conn))
        self.w.close_vnc(self.conn)
        self.assertFalse(self.marker(self.conn))

    def test_double_click_on_vnc_only_device_opens_vnc_not_ssh(self):
        self.w._select_connection(self.conn.id)
        with mock.patch.object(launcher, "launch", side_effect=AssertionError("kein SSH")):
            self.w.connect_selected()
        self.assertTrue(self.w.vnc.is_open(self.conn.id))

    def test_ssh_connection_without_vnc_gets_a_hint(self):
        self.w.open_vnc(self.plain)
        self.assertFalse(self.w.vnc.is_open(self.plain.id))

    def test_missing_viewer_shows_error_dialog(self):
        self.w.cfg.viewers.clear()
        with mock.patch("PySide6.QtWidgets.QMessageBox.critical") as box:
            self.w.open_vnc(self.conn)
        box.assert_called_once()
        self.assertFalse(self.w.vnc.is_open(self.conn.id))

    def test_context_menu_offers_vnc_only_when_enabled(self):
        from PySide6.QtWidgets import QMenu

        texts = []
        for conn in (self.conn, self.plain):
            self.w._select_connection(conn.id)
            menu = QMenu()
            self.w._vnc_menu(menu, conn)
            texts.append([a.text() for a in menu.actions()])
        self.assertEqual(texts[1], [])
        self.assertEqual(len(texts[0]), 1)
        self.w.open_vnc(self.conn)
        menu = QMenu()
        self.w._vnc_menu(menu, self.conn)
        self.assertNotEqual([a.text() for a in menu.actions()], texts[0])  # jetzt „VNC beenden“

    def test_poll_removes_marker_when_viewer_ends(self):
        self.w.open_vnc(self.conn)
        proc = self.w.vnc.sessions[self.conn.id].proc
        proc.terminate()
        self.assertTrue(wait_for(lambda: proc.poll() is not None))
        self.w._poll_tunnels()
        self.assertFalse(self.marker(self.conn))

    def test_password_change_goes_to_vnc_key_only(self):
        c = Connection("N", "h", vnc="direct")
        self.w._apply_vnc_password(c, "pw")
        self.assertEqual(self.stored, {vnc.password_key(c.id): "pw"})
        self.assertTrue(c.vnc_use_password and not c.use_password)
        self.w._apply_vnc_password(c, "")
        self.assertEqual(self.stored, {})
        self.assertFalse(c.vnc_use_password)

    def test_delete_removes_vnc_password_and_closes_session(self):
        self.stored[vnc.password_key(self.conn.id)] = "x"
        self.w.open_vnc(self.conn)
        with mock.patch("PySide6.QtWidgets.QMessageBox.question", return_value=__import__("PySide6.QtWidgets", fromlist=["QMessageBox"]).QMessageBox.StandardButton.Yes):
            self.w.delete_connections([self.conn])
        self.assertEqual(self.stored, {})
        self.assertFalse(self.w.vnc.is_open(self.conn.id))

    def test_viewer_removal_resets_connection_reference(self):
        conn = self.w.cfg.connection_by_id(self.conn.id)  # das Fenster hat seine eigenen Objekte aus der Datei geladen
        conn.vnc_viewer_id = self.viewer.id
        from sesam.gui import main_window

        class Fake:
            def __init__(self, *_a):
                pass

            def exec(self):
                return True

            def result_values(self):
                return [], ""

        with mock.patch.object(main_window, "ViewersDialog", Fake):
            self.w.edit_viewers()
        self.assertEqual((self.w.cfg.viewers, conn.vnc_viewer_id), ([], ""))

    def test_closing_the_window_ends_sessions_but_a_direct_viewer_keeps_running(self):
        self.w.open_vnc(self.conn)
        proc = self.w.vnc.sessions[self.conn.id].proc
        self.addCleanup(proc.terminate)
        self.w.close()
        self.assertEqual(self.w.vnc.sessions, {})
        self.assertIsNone(proc.poll())  # wie ein Terminal: das Fenster des Viewers bleibt offen

    def test_viewer_action_is_in_the_sidebar(self):
        self.assertIn("a_viewers", [n for g in self.w.SIDEBAR_GROUPS for n in g])


if __name__ == "__main__":
    unittest.main()
