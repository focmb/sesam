"""Integriertes Terminal: Tastenzuordnung, Pseudoterminal und Bildschirmauswertung (ohne Anzeige)."""
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    import pyte  # noqa: F401
    from sesam.gui import terminal_view
    HAVE = True
except ImportError:  # PySide6 oder pyte fehlt
    HAVE = False

from sesam import launcher, terminals
from sesam.models import Config, Connection, Terminal


class IntegratedTerminalConfigTests(unittest.TestCase):
    def test_integrated_terminal_is_always_offered(self):
        found = terminals.default_terminals()
        self.assertTrue(any(t.embedded and t.id == terminals.INTEGRATED_ID for t in found))
        again = list(found)
        terminals.ensure_integrated(again)
        self.assertEqual(len(again), len(found))  # nicht doppelt

    def test_launch_without_gui_reports_a_clear_error(self):
        term = terminals.integrated_terminal()
        cfg = Config(terminals=[term], default_terminal_id=term.id)
        launcher.set_embedded_opener(None)
        with self.assertRaises(launcher.LaunchError):
            launcher.launch(cfg, Connection("n", "example.org"), term)

    def test_launch_hands_the_wrapper_to_the_opener(self):
        term = terminals.integrated_terminal()
        cfg = Config(terminals=[term], default_terminal_id=term.id)
        conn = Connection("Server", "example.org")
        cfg.connections.append(conn)
        calls = []

        class Session:
            def poll(self):
                return None

        launcher.set_embedded_opener(lambda args, title, env: calls.append((args, title)) or Session())
        try:
            launcher.launch(cfg, conn, term)
            self.assertEqual(calls[0][1], "Server")
            self.assertIn("--connect", calls[0][0])
            self.assertTrue(launcher.is_open(conn.id))
        finally:
            launcher.set_embedded_opener(None)
            launcher._terminals.clear()


@unittest.skipUnless(HAVE, "PySide6 und pyte nötig")
class KeyTests(unittest.TestCase):
    def k(self, key, text="", mods=Qt.KeyboardModifier.NoModifier, app=False):
        return terminal_view.key_bytes(key, text, mods, app)

    def test_plain_text_and_umlauts(self):
        self.assertEqual(self.k(Qt.Key.Key_A, "a"), b"a")
        self.assertEqual(self.k(Qt.Key.Key_Adiaeresis, "ä"), "ä".encode())

    def test_control_characters(self):
        ctrl = Qt.KeyboardModifier.ControlModifier
        self.assertEqual(self.k(Qt.Key.Key_C, "\x03", ctrl), b"\x03")
        self.assertEqual(self.k(Qt.Key.Key_D, "", ctrl), b"\x04")
        self.assertEqual(self.k(Qt.Key.Key_Space, " ", ctrl), b"\x00")

    def test_alt_sends_escape_prefix_but_altgr_sends_the_character(self):
        alt, ctrl = Qt.KeyboardModifier.AltModifier, Qt.KeyboardModifier.ControlModifier
        self.assertEqual(self.k(Qt.Key.Key_B, "b", alt), b"\x1bb")
        self.assertEqual(self.k(Qt.Key.Key_At, "@", alt | ctrl), b"@")

    def test_cursor_keys_follow_the_application_mode(self):
        self.assertEqual(self.k(Qt.Key.Key_Up), b"\x1b[A")
        self.assertEqual(self.k(Qt.Key.Key_Up, app=True), b"\x1bOA")
        self.assertEqual(self.k(Qt.Key.Key_Left, "", Qt.KeyboardModifier.ControlModifier), b"\x1b[1;5D")
        self.assertEqual(self.k(Qt.Key.Key_Delete), b"\x1b[3~")
        self.assertEqual(self.k(Qt.Key.Key_F5), b"\x1b[15~")
        self.assertEqual(self.k(Qt.Key.Key_Backspace), b"\x7f")

    def test_modifier_alone_sends_nothing(self):
        self.assertIsNone(self.k(Qt.Key.Key_Shift))


@unittest.skipUnless(HAVE and not sys.platform.startswith("win"), "PySide6, pyte und ein POSIX-Pseudoterminal nötig")
class SessionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def pump(self, until, timeout=5.0):
        end = time.time() + timeout
        while time.time() < end:
            self.app.processEvents()
            if until():
                return True
            time.sleep(0.01)
        return False

    def text(self, view):
        return "\n".join(line.rstrip() for line in view._screen.display)

    def start(self, script):
        view = terminal_view.TerminalView("t")
        view.resize(800, 400)
        view.show()
        codes = []
        view.closed.connect(codes.append)
        view.start(["bash", "-c", script], dict(os.environ))
        return view, codes

    def test_output_exit_code_and_size(self):
        view, codes = self.start("echo hallo; stty size; exit 3")
        self.assertTrue(self.pump(lambda: bool(codes)))
        self.assertEqual(codes, [3])
        text = self.text(view)
        self.assertIn("hallo", text)
        self.assertIn(f"{view._rows} {view._cols}", text)
        self.assertEqual(view.poll(), 3)

    def test_colors_and_input(self):
        view, codes = self.start("printf '\\033[31mrot\\033[0m\\n'; read x; echo got:$x")
        self.assertTrue(self.pump(lambda: "rot" in self.text(view)))
        self.assertEqual(view._screen.buffer[0][0].fg, "red")
        view._send(b"abc\r")
        self.assertTrue(self.pump(lambda: bool(codes)))
        self.assertIn("got:abc", self.text(view))

    def test_scrollback_and_alternate_screen(self):
        view, codes = self.start("seq 1 200; printf '\\033[?1049h\\033[2JALT'; sleep 0.3; printf '\\033[?1049l'; echo ende")
        self.assertTrue(self.pump(lambda: bool(codes)))
        self.assertGreater(len(view._screen.scrollback), 150)
        self.assertFalse(view._screen.in_alt)
        self.assertIn("ende", self.text(view))
        self.assertNotIn("ALT", self.text(view))  # der Alternativbildschirm hinterlässt nichts
        self.assertEqual("".join(c.data for c in view._screen.scrollback[0]).strip(), "1")

    def test_terminate_stops_the_process(self):
        view, codes = self.start("sleep 30")
        self.assertTrue(self.pump(lambda: view._pty is not None))
        view.terminate()
        self.assertTrue(self.pump(lambda: bool(codes)))
        self.assertIsNotNone(view.poll())

    def test_start_failure_is_shown_in_the_terminal(self):
        view = terminal_view.TerminalView("t")
        view.resize(800, 400)
        view.show()
        view.start(["/nonexistent/programm"], dict(os.environ))
        self.assertTrue(self.pump(lambda: view.failed_to_start()))
        self.assertIn("nonexistent", self.text(view))
        self.assertEqual(view.poll(), -1)


if __name__ == "__main__":
    unittest.main()


try:
    from gui_base import GuiTestCase
except ImportError:  # PySide6 fehlt
    GuiTestCase = None

if GuiTestCase is not None and HAVE and not sys.platform.startswith("win"):

    class MainWindowTabTests(GuiTestCase):
        """Das integrierte Terminal im Hauptfenster: Reiter öffnen, schließen, beim Beenden nachfragen."""

        def setUp(self):
            super().setUp()
            from unittest import mock
            from sesam import runtime, storage

            cfg = Config(terminals=[terminals.integrated_terminal()], default_terminal_id=terminals.INTEGRATED_ID)
            self.conn = Connection("Test", "localhost")
            cfg.connections.append(self.conn)
            storage.save(cfg, self.path)
            # Statt Sesam selbst (ssh) läuft eine kleine Shell, die nach einer Eingabe endet
            patcher = mock.patch.object(launcher, "wrapper_args", lambda conn, remote_command=None: ["bash", "-c", "echo sitzung; read x"])
            patcher.start()
            self.addCleanup(patcher.stop)
            self.w = self._window()
            self.addCleanup(self._shutdown)

        def _shutdown(self):
            # erst alle Sitzungen beenden und abwarten, sonst fragt closeEvent (modal, im Test gesperrt)
            for v in self.w._terminal_views():
                v.terminate(force=True)
            self.pump(lambda: all(v.poll() is not None for v in self.w._terminal_views()), timeout=10)
            self.w.close()

        def pump(self, until, timeout=5.0):
            end = time.time() + timeout
            while time.time() < end:
                self.app.processEvents()
                if until():
                    return True
                time.sleep(0.01)
            return False

        def test_session_opens_as_tab_and_closes_with_the_process(self):
            self.assertEqual(self.w.tabs.count(), 1)
            self.w._open_connection(self.conn)
            self.assertEqual(self.w.tabs.count(), 2)
            self.assertEqual(self.w.tabs.tabText(1), "Test")
            self.assertEqual(self.w.tabs.currentIndex(), 1)
            view = self.w.tabs.widget(1)
            self.assertTrue(self.pump(lambda: "sitzung" in "\n".join(view._screen.display)))
            self.assertTrue(launcher.is_open(self.conn.id))
            view._send(b"\r")  # read x -> Prozess endet
            self.assertTrue(self.pump(lambda: self.w.tabs.count() == 1))
            self.assertFalse(launcher.is_open(self.conn.id))

        def test_closing_a_running_tab_asks_first(self):
            from unittest import mock
            from PySide6.QtWidgets import QMessageBox

            self.w._open_connection(self.conn)
            view = self.w.tabs.widget(1)
            self.assertTrue(self.pump(lambda: view._pty is not None))
            with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
                self.w._close_terminal_tab(1)
            self.assertEqual(self.w.tabs.count(), 2)
            with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
                self.w._close_terminal_tab(1)
            self.assertTrue(self.pump(lambda: self.w.tabs.count() == 1))

        def test_quitting_with_a_running_session_can_be_cancelled(self):
            from unittest import mock
            from PySide6.QtGui import QCloseEvent
            from PySide6.QtWidgets import QMessageBox

            self.w._open_connection(self.conn)
            view = self.w.tabs.widget(1)
            self.assertTrue(self.pump(lambda: view._pty is not None))
            event = QCloseEvent()
            with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
                self.w.closeEvent(event)
            self.assertFalse(event.isAccepted())
            event = QCloseEvent()
            with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
                self.w.closeEvent(event)
            self.assertTrue(self.pump(lambda: view.poll() is not None))
