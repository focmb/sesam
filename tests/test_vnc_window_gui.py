"""Eingebauter VNC-Viewer: Fenster (Anzeige, Eingabe, Zwischenablage, Abbruch, Passwort), Tastenzuordnung, Einbindung ins Hauptfenster."""
import struct
import time
import unittest
from unittest import mock

from gui_base import GuiTestCase

from sesam import storage, viewers
from sesam.models import Config, Connection, Viewer
from test_rfb import FakeServer, px, rect, update


def pump(cond, timeout=8.0):
    """Verarbeitet Qt-Ereignisse, bis ``cond()`` wahr ist."""
    from PySide6.QtCore import QCoreApplication

    end = time.monotonic() + timeout
    while time.monotonic() < end:
        QCoreApplication.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    QCoreApplication.processEvents()
    return bool(cond())


class KeysymTests(unittest.TestCase):
    def setUp(self):
        from PySide6.QtCore import Qt

        self.Qt = Qt

    def sym(self, key, text="", shift=False):
        from sesam.gui.vnc_window import keysym_for

        return keysym_for(int(getattr(key, "value", key)), text, shift)

    def test_special_keys(self):
        K = self.Qt.Key
        self.assertEqual(self.sym(K.Key_Return), 0xFF0D)
        self.assertEqual(self.sym(K.Key_Backspace), 0xFF08)
        self.assertEqual(self.sym(K.Key_Tab), 0xFF09)
        self.assertEqual(self.sym(K.Key_Backtab), 0xFF09)
        self.assertEqual(self.sym(K.Key_Escape), 0xFF1B)
        self.assertEqual(self.sym(K.Key_Delete), 0xFFFF)
        self.assertEqual(self.sym(K.Key_Left), 0xFF51)
        self.assertEqual(self.sym(K.Key_Control), 0xFFE3)
        self.assertEqual(self.sym(K.Key_F1), 0xFFBE)
        self.assertEqual(self.sym(K.Key_F12), 0xFFC9)

    def test_characters(self):
        K = self.Qt.Key
        self.assertEqual(self.sym(K.Key_A, "a"), 0x61)
        self.assertEqual(self.sym(K.Key_A, "A", shift=True), 0x41)
        self.assertEqual(self.sym(K.Key_Adiaeresis, "ä"), 0xE4)
        self.assertEqual(self.sym(K.Key_unknown, "€"), 0x010020AC)
        self.assertEqual(self.sym(K.Key_Space, " "), 0x20)

    def test_control_combinations_use_the_key_not_the_control_character(self):
        K = self.Qt.Key
        self.assertEqual(self.sym(K.Key_A, "\x01"), 0x61)  # Strg+A
        self.assertEqual(self.sym(K.Key_A, "\x01", shift=True), 0x41)
        self.assertEqual(self.sym(K.Key_1, "\x00"), 0x31)

    def test_altgr_is_not_sent_and_unknown_keys_are_ignored(self):
        K = self.Qt.Key
        self.assertIsNone(self.sym(K.Key_AltGr))
        self.assertIsNone(self.sym(K.Key_unknown))


class WindowTests(GuiTestCase):
    def open(self, password=None, direct=True, server_password="geheim", **server_args):
        from sesam.gui.vnc_window import VncWindow

        server = FakeServer(password=server_password, **server_args)
        self.addCleanup(server.close)
        window = VncWindow("Testgerät", "127.0.0.1", server.port, password, direct)
        self.addCleanup(window.end)
        window.show()
        return server, window

    def connected(self, window):
        self.assertTrue(pump(lambda: window.view.image is not None and window._client() is not None), "keine Verbindung")

    def pixel(self, window, x, y):
        c = window.view.image.pixelColor(x, y)
        return (c.red(), c.green(), c.blue())

    def test_shows_the_screen_and_updates(self):
        server, window = self.open(width=64, height=48)
        self.connected(window)
        self.assertEqual((window.view.image.width(), window.view.image.height()), (64, 48))
        self.assertIn("Testserver", window.status_text.text())
        self.assertIn("64×48", window.status_text.text())
        server.push(update(rect(5, 6, 2, 1, 0, px(10, 20, 30) + px(40, 50, 60))))
        self.assertTrue(pump(lambda: self.pixel(window, 5, 6) == (10, 20, 30)))
        self.assertEqual(self.pixel(window, 6, 6), (40, 50, 60))

    def test_security_hint_depends_on_the_connection(self):
        _, direct = self.open(direct=True)
        _, tunnel = self.open(direct=False)
        self.assertIn("unverschlüsselt", direct.security_text.text())
        self.assertIn("SSH-Tunnel", tunnel.security_text.text())

    def test_desktop_resize(self):
        server, window = self.open(width=64, height=48)
        self.connected(window)
        server.push(update(rect(0, 0, 30, 20, -223)))
        self.assertTrue(pump(lambda: window.view.image.width() == 30))
        self.assertEqual(window.view.image.height(), 20)

    def test_mouse_keyboard_and_view_only(self):
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtTest import QTest

        server, window = self.open(width=64, height=48)
        self.connected(window)
        window.a_fit.setChecked(False)  # Originalgröße: Widget-Koordinaten = Bildkoordinaten
        pump(lambda: False, 0.1)
        QTest.mouseClick(window.view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(10, 5))
        self.assertTrue(server.wait_for("pointer", 2))
        pointers = server.of("pointer")
        self.assertIn((1, 10, 5), pointers)
        self.assertEqual(pointers[-1], (0, 10, 5))
        QTest.keyClick(window.view, Qt.Key.Key_A)
        self.assertTrue(server.wait_for("key", 2))
        self.assertEqual(server.of("key")[-2:], [(1, 0x61), (0, 0x61)])
        window.a_view_only.setChecked(True)
        before = len(server.of("pointer")), len(server.of("key"))
        QTest.mouseClick(window.view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(3, 3))
        QTest.keyClick(window.view, Qt.Key.Key_B)
        pump(lambda: False, 0.2)
        self.assertEqual((len(server.of("pointer")), len(server.of("key"))), before)  # „Nur Ansicht“ sendet nichts

    def test_scaled_mouse_position_maps_to_the_remote_screen(self):
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtTest import QTest

        server, window = self.open(width=100, height=50)
        self.connected(window)
        window.resize(400, 300)
        window.a_fit.setChecked(True)
        pump(lambda: False, 0.2)
        view = window.view
        ox, oy, scale = view._geometry()
        QTest.mouseMove(view, QPoint(int(ox + 50 * scale), int(oy + 25 * scale)))
        self.assertTrue(server.wait_for("pointer"))
        x, y = server.of("pointer")[-1][1:]
        self.assertLessEqual(abs(x - 50), 1)
        self.assertLessEqual(abs(y - 25), 1)

    def test_ctrl_alt_del(self):
        server, window = self.open()
        self.connected(window)
        window.a_cad.trigger()
        self.assertTrue(server.wait_for("key", 6))
        self.assertEqual(server.of("key"), [(1, 0xFFE3), (1, 0xFFE9), (1, 0xFFFF), (0, 0xFFFF), (0, 0xFFE9), (0, 0xFFE3)])

    def test_pressed_keys_are_released_when_focus_is_lost(self):
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QKeyEvent
        from PySide6.QtCore import QEvent

        server, window = self.open()
        self.connected(window)
        window.key_event(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Shift, Qt.KeyboardModifier.ShiftModifier), True)
        self.assertTrue(server.wait_for("key"))
        window.release_keys()
        self.assertTrue(server.wait_for("key", 2))
        self.assertEqual(server.of("key"), [(1, 0xFFE1), (0, 0xFFE1)])

    def test_clipboard_in_both_directions(self):
        from PySide6.QtGui import QGuiApplication

        server, window = self.open()
        self.connected(window)
        text = "Grüße".encode("latin-1")
        server.push(struct.pack(">Bxxxi", 3, len(text)) + text)
        self.assertTrue(pump(lambda: QGuiApplication.clipboard().text() == "Grüße"))
        QGuiApplication.clipboard().setText("lokal")
        window._send_clipboard()
        self.assertTrue(server.wait_for("cut_text"))
        self.assertEqual(server.of("cut_text")[-1], ("lokal",))
        pump(lambda: False, 0.3)  # bei aktivem Fenster geht der Text auch schon beim Kopieren hinaus: erst abwarten, dann zählen
        sent = len(server.of("cut_text"))
        window.a_clipboard.setChecked(False)  # abgeschaltet: nichts geht hinaus, nichts kommt herein
        QGuiApplication.clipboard().setText("geheim")
        window._send_clipboard()
        server.push(struct.pack(">Bxxxi", 3, 3) + b"neu")
        pump(lambda: False, 0.3)
        self.assertEqual(len(server.of("cut_text")), sent)
        self.assertEqual(QGuiApplication.clipboard().text(), "geheim")

    def test_cursor_from_the_server_is_used(self):
        from PySide6.QtCore import Qt

        server, window = self.open()
        self.connected(window)
        server.push(update(rect(1, 1, 2, 2, -239, px(255, 0, 0) * 4 + bytes([0b11000000, 0b11000000]))))
        self.assertTrue(pump(lambda: window.view.cursor().shape() == Qt.CursorShape.BitmapCursor))
        server.push(update(rect(0, 0, 0, 0, -239)))
        self.assertTrue(pump(lambda: window.view.cursor().shape() == Qt.CursorShape.BlankCursor))

    def test_dropped_connection_shows_a_message_and_offers_reconnect(self):
        server, window = self.open()
        self.connected(window)
        reconnect = []
        window.reconnect_requested.connect(lambda: reconnect.append(1))
        self.assertFalse(window.a_reconnect.isEnabled())
        server.drop()
        self.assertTrue(pump(lambda: window.a_reconnect.isEnabled()))
        self.assertIn("getrennt", window.view.message)
        window.a_reconnect.trigger()
        self.assertEqual(reconnect, [1])

    def test_connection_refused_is_shown_not_raised(self):
        import socket

        from sesam.gui.vnc_window import VncWindow

        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        window = VncWindow("x", "127.0.0.1", port, None, True)
        self.addCleanup(window.end)
        self.assertTrue(pump(lambda: window.a_reconnect.isEnabled()))
        self.assertIn("Nicht verbunden", window.view.message)

    def test_password_is_asked_for_when_the_server_wants_one(self):
        from PySide6.QtWidgets import QInputDialog

        with mock.patch.object(QInputDialog, "getText", return_value=("geheim", True)) as ask:
            server, window = self.open(security=(2,))
            self.connected(window)
        self.assertEqual(ask.call_count, 1)

    def test_wrong_stored_password_asks_again_and_cancel_stops(self):
        from PySide6.QtWidgets import QInputDialog

        server = FakeServer(security=(2,), password="geheim")
        self.addCleanup(server.close)
        from sesam.gui.vnc_window import VncWindow

        with mock.patch.object(QInputDialog, "getText", return_value=("geheim", True)) as ask:
            window = VncWindow("x", "127.0.0.1", server.port, "falsch", True)
            self.addCleanup(window.end)
            self.connected(window)
        self.assertEqual(ask.call_count, 1)
        server2 = FakeServer(security=(2,), password="geheim")
        self.addCleanup(server2.close)
        with mock.patch.object(QInputDialog, "getText", return_value=("", False)):
            window2 = VncWindow("y", "127.0.0.1", server2.port, None, True)
            self.addCleanup(window2.end)
            self.assertTrue(pump(lambda: window2.a_reconnect.isEnabled()))
        self.assertIn("Passwort", window2.view.message)

    def test_closing_while_connecting_does_not_hang_or_crash(self):
        import socket

        from sesam.gui.vnc_window import VncWindow

        silent = socket.socket()
        silent.bind(("127.0.0.1", 0))
        silent.listen(1)
        self.addCleanup(silent.close)
        window = VncWindow("x", "127.0.0.1", silent.getsockname()[1], None, True)
        pump(lambda: False, 0.3)
        start = time.monotonic()
        window.end()
        self.assertLess(time.monotonic() - start, 3.5)
        self.assertFalse(window.is_open())

    def test_end_closes_window_and_connection(self):
        server, window = self.open()
        self.connected(window)
        self.assertTrue(window.is_open())
        window.end()
        self.assertFalse(window.is_open())
        window.end()  # mehrfach ist unschädlich
        self.assertIsNone(window.reader)


class IntegrationTests(GuiTestCase):
    def test_main_window_opens_the_builtin_viewer_and_ends_the_session_with_it(self):
        server = FakeServer(width=40, height=30)
        self.addCleanup(server.close)
        conn = Connection("Gerät", "127.0.0.1", remote="vnc", remote_mode="direct", remote_port=server.port)
        storage.save(Config(connections=[conn], viewers=[viewers.builtin_viewer()], default_viewer_id=viewers.BUILTIN_ID), self.path)
        w = self._window()
        self.addCleanup(w.remote.stop_all)
        w.open_remote(conn)
        window = w.remote.sessions[conn.id].window
        self.addCleanup(window.end)
        self.assertTrue(pump(lambda: window.view.image is not None))
        self.assertTrue(w.remote.is_open(conn.id))
        window.close()
        w.remote.poll(w.cfg)
        self.assertFalse(w.remote.is_open(conn.id))

    def test_reconnect_opens_a_new_session(self):
        server = FakeServer(width=40, height=30)
        self.addCleanup(server.close)
        conn = Connection("Gerät", "127.0.0.1", remote="vnc", remote_mode="direct", remote_port=server.port)
        storage.save(Config(connections=[conn], viewers=[viewers.builtin_viewer()], default_viewer_id=viewers.BUILTIN_ID), self.path)
        w = self._window()
        self.addCleanup(w.remote.stop_all)
        w.open_remote(conn)
        first = w.remote.sessions[conn.id].window
        self.addCleanup(first.end)
        w._reconnect_remote(conn.id)
        second = w.remote.sessions[conn.id].window
        self.addCleanup(second.end)
        self.assertIsNot(first, second)
        self.assertFalse(first.is_open())
        self.assertTrue(second.is_open())


class DialogTests(GuiTestCase):
    def test_builtin_viewer_is_listed_but_not_editable_or_removable(self):
        from sesam.gui.viewers_dialog import ViewersDialog

        mine = Viewer("Mein Viewer", "meinviewer {host}")
        cfg = Config(viewers=[viewers.builtin_viewer(), mine], default_viewer_id=viewers.BUILTIN_ID)
        d = ViewersDialog(cfg)
        self.addCleanup(d.close)
        d.list.setCurrentRow(0)
        self.assertFalse(d.name.isEnabled())
        self.assertFalse(d.command.isEnabled())
        self.assertFalse(d.remove_button.isEnabled())
        self.assertFalse(d.builtin_note.isHidden())
        d._remove()
        self.assertEqual(len(d.viewers), 2)
        d.list.setCurrentRow(1)
        self.assertTrue(d.name.isEnabled())
        self.assertTrue(d.remove_button.isEnabled())
        self.assertTrue(d.builtin_note.isHidden())
        _, vnc_default, _ = d.result_values()
        self.assertEqual(vnc_default, viewers.BUILTIN_ID)


if __name__ == "__main__":
    unittest.main()
