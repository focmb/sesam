"""Fernzugriff (VNC/RDP) in der Oberfläche: Verbindungsdialog, Viewer-Verwaltung, Hauptfenster (Start, Markierung, Passwort, Löschen)."""
import sys
import unittest
from pathlib import Path
from unittest import mock

from gui_base import GuiTestCase

from sesam import launcher, passwords, remote, storage
from sesam.models import Config, Connection, Viewer
from test_remote import wait_for


def _cfg_with_viewers() -> Config:
    return Config(viewers=[Viewer("A", "a"), Viewer("B", "b"), Viewer("R", "r", protocol="rdp")])


class DialogTests(GuiTestCase):
    def dialog(self, conn=None, cfg=None):
        from sesam.gui.connection_dialog import ConnectionDialog

        d = ConnectionDialog(cfg or _cfg_with_viewers(), conn)
        self.addCleanup(d.close)
        return d

    def pick(self, combo, data):
        combo.setCurrentIndex(combo.findData(data))

    def test_fields_are_disabled_while_off_and_follow_the_protocol(self):
        d = self.dialog()
        for w in (d.remote_mode, d.remote_host, d.remote_port, d.remote_viewer, d.remote_password, d.remote_user, d.remote_domain, d.remote_args):
            self.assertFalse(w.isEnabled())
        self.pick(d.remote_protocol, "vnc")
        self.assertTrue(d.remote_port.isEnabled() and d.remote_password.isEnabled() and d.remote_host.isEnabled())  # Standardart: Tunnel
        self.assertFalse(d.remote_user.isEnabled())  # Benutzer, Domäne und Argumente gibt es nur bei RDP
        self.pick(d.remote_mode, "direct")
        self.assertFalse(d.remote_host.isEnabled())  # „Ziel vom Server aus“ gibt es nur beim Tunnel
        self.pick(d.remote_protocol, "rdp")
        self.assertTrue(d.remote_user.isEnabled() and d.remote_domain.isEnabled() and d.remote_args.isEnabled())

    def test_port_follows_the_protocol_default_until_changed(self):
        d = self.dialog()
        self.pick(d.remote_protocol, "vnc")
        self.assertEqual(d.remote_port.value(), 5900)
        self.pick(d.remote_protocol, "rdp")
        self.assertEqual(d.remote_port.value(), 3389)
        d.remote_port.setValue(3390)
        self.pick(d.remote_protocol, "vnc")
        self.assertEqual(d.remote_port.value(), 3390)  # ein eigener Port bleibt

    def test_viewer_choices_are_filtered_by_protocol(self):
        d = self.dialog()
        self.pick(d.remote_protocol, "vnc")
        self.assertEqual([d.remote_viewer.itemText(i) for i in range(d.remote_viewer.count())][1:], ["A", "B"])
        self.pick(d.remote_protocol, "rdp")
        self.assertEqual([d.remote_viewer.itemText(i) for i in range(d.remote_viewer.count())][1:], ["R"])

    def test_vnc_values_are_saved_and_password_change_reported(self):
        cfg = _cfg_with_viewers()
        d = self.dialog(cfg=cfg)
        d.name.setText("PC")
        d.host.setText("srv")
        self.pick(d.remote_protocol, "vnc")
        self.pick(d.remote_mode, "tunnel")
        d.remote_host.setText("gerät")
        d.remote_port.setValue(5901)
        self.pick(d.remote_viewer, cfg.viewers[1].id)
        d.remote_password.setText("geheim")
        d._accept()
        c = d.result_connection()
        self.assertEqual((c.remote, c.remote_mode, c.remote_host, c.remote_port, c.remote_viewer_id),
                         ("vnc", "tunnel", "gerät", 5901, cfg.viewers[1].id))
        self.assertEqual((c.remote_user, c.remote_domain, c.remote_args), ("", "", ""))
        self.assertEqual(d.remote_password_change, "geheim")
        self.assertIsNone(d.password_change)  # das SSH-Passwort bleibt davon unberührt

    def test_rdp_values_are_saved_and_default_port_is_stored_as_zero(self):
        d = self.dialog()
        d.name.setText("PC")
        d.host.setText("srv")
        self.pick(d.remote_protocol, "rdp")
        d.remote_user.setText(" ich ")
        d.remote_domain.setText("CORP")
        d.remote_args.setText("/f")
        d._accept()
        c = d.result_connection()
        self.assertEqual((c.remote, c.remote_port, c.remote_user, c.remote_domain, c.remote_args), ("rdp", 0, "ich", "CORP", "/f"))

    def test_rdp_fields_are_cleared_when_switching_back_to_vnc(self):
        conn = Connection("PC", "h", remote="rdp", remote_user="ich", remote_domain="X", remote_args="/f")
        d = self.dialog(conn)
        self.pick(d.remote_protocol, "vnc")
        d._accept()
        self.assertEqual((conn.remote, conn.remote_user, conn.remote_domain, conn.remote_args), ("vnc", "", "", ""))
        self.assertEqual(d.remote_before, ("rdp", False))

    def test_existing_password_is_kept_cleared_or_replaced(self):
        conn = Connection("PC", "h", remote="vnc", remote_mode="direct", remote_use_password=True)
        d = self.dialog(conn)
        d._accept()
        self.assertIsNone(d.remote_password_change)
        d = self.dialog(conn)
        d.remote_clear_password.setChecked(True)
        d._accept()
        self.assertEqual(d.remote_password_change, "")

    def test_host_only_kept_for_tunnel_mode(self):
        conn = Connection("PC", "h", remote="vnc", remote_mode="tunnel", remote_host="x")
        d = self.dialog(conn)
        self.pick(d.remote_mode, "direct")
        d._accept()
        self.assertEqual(conn.remote_host, "")


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
        viewers, default, rdp_default = d.result_values()
        self.assertEqual((viewers[1].command, default, rdp_default), ("bb {host}", b.id, ""))
        d._add()
        self.assertEqual(d.list.count(), 3)
        d.list.setCurrentRow(1)
        d._remove()
        viewers, default, _ = d.result_values()
        self.assertEqual([v.name for v in viewers], ["A", tr_own()])
        self.assertEqual(default, a.id)  # der gelöschte Standard-Viewer wird ersetzt

    def test_list_shows_only_the_chosen_protocol_with_separate_defaults(self):
        v, r = Viewer("V", "v"), Viewer("R", "r", protocol="rdp")
        d = self.dialog(Config(viewers=[v, r], default_viewer_id=v.id, default_rdp_viewer_id=r.id))
        self.assertEqual([d.list.item(i).text() for i in range(d.list.count())], [f"{v.name} (Standard)"])
        d.protocol_box.setCurrentIndex(d.protocol_box.findData("rdp"))
        self.assertEqual([d.list.item(i).text() for i in range(d.list.count())], [f"{r.name} (Standard)"])
        d._add()
        self.assertEqual(d.viewers[-1].protocol, "rdp")  # neue Viewer gehören zum gewählten Protokoll
        self.assertEqual(d.list.count(), 2)
        viewers, vnc_default, rdp_default = d.result_values()
        self.assertEqual((vnc_default, rdp_default), (v.id, r.id))
        d._remove()  # entfernt den (gewählten) Standard-Viewer von RDP
        self.assertEqual(d.result_values()[2], d.viewers[-1].id)

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
        self.rdp_viewer = Viewer("fake-rdp", f'"{sys.executable}" "{script}" {{host}} {{port}}', protocol="rdp")
        self.conn = Connection("Gerät", "10.1.2.3", remote="vnc", remote_mode="direct", remote_use_password=True)
        self.plain = Connection("Server", "srv")
        storage.save(Config(connections=[self.conn, self.plain], viewers=[self.viewer, self.rdp_viewer], default_viewer_id=self.viewer.id,
                            default_rdp_viewer_id=self.rdp_viewer.id), self.path)
        self.w = self._window()
        self.addCleanup(self.w.remote.stop_all)
        self.stored: dict = {}
        for name, fake in (("get_password", lambda i: "geheim"), ("set_password", lambda i, p: self.stored.__setitem__(i, p)),
                           ("delete_password", lambda i: self.stored.pop(i, None))):
            patcher = mock.patch.object(passwords, name, fake)
            patcher.start()
            self.addCleanup(patcher.stop)

    def marker(self, conn):
        from sesam.gui.main_window import OPEN_ROLE

        return bool(self.w._find_item(conn.id).data(0, OPEN_ROLE))

    def test_open_remote_shows_green_dot_and_close_removes_it(self):
        self.assertFalse(self.marker(self.conn))
        self.w.open_remote(self.conn)
        self.assertTrue(self.w.remote.is_open(self.conn.id))
        self.assertTrue(self.marker(self.conn))
        self.w.close_remote(self.conn)
        self.assertFalse(self.marker(self.conn))

    def test_rdp_session_opens_with_the_rdp_viewer(self):
        conn = Connection("Win", "10.9.9.9", remote="rdp", remote_mode="direct")
        self.w.open_remote(conn)
        self.assertEqual(self.w.remote.sessions[conn.id].viewer.name, "fake-rdp")

    def test_double_click_on_vnc_or_rdp_only_device_opens_remote_not_ssh(self):
        self.w._select_connection(self.conn.id)
        with mock.patch.object(launcher, "launch", side_effect=AssertionError("kein SSH")):
            self.w.connect_selected()
        self.assertTrue(self.w.remote.is_open(self.conn.id))

    def test_tunnel_connection_keeps_ssh_on_double_click(self):
        conn = self.w.cfg.connection_by_id(self.conn.id)
        conn.remote_mode = "tunnel"
        self.w._select_connection(conn.id)
        with mock.patch.object(self.w, "_open_connection") as opened:
            self.w.connect_selected()
        opened.assert_called_once()
        self.assertFalse(self.w.remote.is_open(conn.id))

    def test_ssh_connection_without_remote_gets_a_hint(self):
        self.w.open_remote(self.plain)
        self.assertFalse(self.w.remote.is_open(self.plain.id))

    def test_missing_viewer_shows_error_dialog(self):
        self.w.cfg.viewers.clear()
        with mock.patch("PySide6.QtWidgets.QMessageBox.critical") as box:
            self.w.open_remote(self.conn)
        box.assert_called_once()
        self.assertFalse(self.w.remote.is_open(self.conn.id))

    def test_context_menu_offers_remote_only_when_enabled_and_names_the_protocol(self):
        from PySide6.QtWidgets import QMenu

        def texts(conn):
            menu = QMenu()
            self.w._remote_menu(menu, conn)
            return [a.text() for a in menu.actions()]

        self.assertEqual(texts(self.plain), [])
        self.assertEqual(texts(self.conn), ["VNC öffnen"])
        self.assertEqual(texts(Connection("Win", "w", remote="rdp")), ["RDP öffnen"])
        self.w.open_remote(self.conn)
        self.assertEqual(texts(self.conn), ["VNC beenden"])

    def test_poll_removes_marker_when_viewer_ends(self):
        self.w.open_remote(self.conn)
        proc = self.w.remote.sessions[self.conn.id].proc
        proc.terminate()
        self.assertTrue(wait_for(lambda: proc.poll() is not None))
        self.w._poll_tunnels()
        self.assertFalse(self.marker(self.conn))

    def test_password_change_goes_to_the_protocol_key_only(self):
        c = Connection("N", "h", remote="rdp", remote_mode="direct")
        self.w._apply_remote_password(c, "pw")
        self.assertEqual(self.stored, {remote.password_key(c.id, "rdp"): "pw"})
        self.assertTrue(c.remote_use_password and not c.use_password)
        self.w._apply_remote_password(c, "")
        self.assertEqual(self.stored, {})
        self.assertFalse(c.remote_use_password)

    def test_switching_protocol_removes_the_old_password(self):
        c = Connection("N", "h", remote="vnc", remote_mode="direct", remote_use_password=True)
        self.stored[remote.password_key(c.id, "vnc")] = "alt"
        c.remote = "rdp"  # der Dialog hat das Protokoll schon geändert
        self.w._apply_remote_password(c, None, ("vnc", True))
        self.assertEqual(self.stored, {})
        self.assertFalse(c.remote_use_password)
        c.remote_use_password = True
        self.stored[remote.password_key(c.id, "rdp")] = "x"
        self.w._apply_remote_password(c, "neu", ("rdp", True))  # unverändertes Protokoll: nur das neue Passwort
        self.assertEqual(self.stored, {remote.password_key(c.id, "rdp"): "neu"})

    def test_delete_removes_remote_passwords_and_closes_session(self):
        self.stored[remote.password_key(self.conn.id, "vnc")] = "x"
        self.stored[remote.password_key(self.conn.id, "rdp")] = "y"
        self.w.open_remote(self.conn)
        from PySide6.QtWidgets import QMessageBox

        with mock.patch("PySide6.QtWidgets.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
            self.w.delete_connections([self.conn])
        self.assertEqual(self.stored, {})
        self.assertFalse(self.w.remote.is_open(self.conn.id))

    def test_viewer_removal_resets_connection_reference(self):
        conn = self.w.cfg.connection_by_id(self.conn.id)  # das Fenster hat seine eigenen Objekte aus der Datei geladen
        conn.remote_viewer_id = self.viewer.id
        from sesam.gui import main_window

        class Fake:
            def __init__(self, *_a):
                pass

            def exec(self):
                return True

            def result_values(self):
                return [], "", ""

        with mock.patch.object(main_window, "ViewersDialog", Fake):
            self.w.edit_viewers()
        self.assertEqual((self.w.cfg.viewers, conn.remote_viewer_id), ([], ""))

    def test_closing_the_window_ends_sessions_but_a_direct_viewer_keeps_running(self):
        self.w.open_remote(self.conn)
        proc = self.w.remote.sessions[self.conn.id].proc
        self.addCleanup(proc.terminate)
        self.w.close()
        self.assertEqual(self.w.remote.sessions, {})
        self.assertIsNone(proc.poll())  # wie ein Terminal: das Fenster des Viewers bleibt offen

    def test_viewer_action_is_in_the_sidebar(self):
        self.assertIn("a_viewers", [n for g in self.w.SIDEBAR_GROUPS for n in g])


if __name__ == "__main__":
    unittest.main()
