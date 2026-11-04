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
            view = self.w._terminal_views()[0]
            self.assertTrue(self.pump(lambda: "sitzung" in "\n".join(view._screen.display)))
            self.assertTrue(launcher.is_open(self.conn.id))
            view._send(b"\r")  # read x -> Prozess endet
            self.assertTrue(self.pump(lambda: self.w.tabs.count() == 1))
            self.assertFalse(launcher.is_open(self.conn.id))

        def test_closing_a_running_tab_asks_first(self):
            from unittest import mock
            from PySide6.QtWidgets import QMessageBox

            self.w._open_connection(self.conn)
            view = self.w._terminal_views()[0]
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
            view = self.w._terminal_views()[0]
            self.assertTrue(self.pump(lambda: view._pty is not None))
            event = QCloseEvent()
            with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
                self.w.closeEvent(event)
            self.assertFalse(event.isAccepted())
            event = QCloseEvent()
            with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
                self.w.closeEvent(event)
            self.assertTrue(self.pump(lambda: view.poll() is not None))

        def _two_tiles(self, orientation=None):
            from PySide6.QtCore import Qt

            self.w._open_connection(self.conn)
            first = self.w._terminal_views()[0]
            self.assertTrue(self.pump(lambda: first._pty is not None))
            self.w._split_view(first, orientation or Qt.Orientation.Horizontal)
            views = self.w._terminal_views()
            self.assertEqual(len(views), 2)
            second = [v for v in views if v is not first][0]
            self.assertTrue(self.pump(lambda: second._pty is not None))
            return first, second

        def test_local_terminal_opens_a_shell_as_tab(self):
            from unittest import mock
            from sesam import localshell

            with mock.patch.object(localshell, "command", lambda: ["bash", "-c", "echo lokal-ok; read x"]):
                self.w.a_local.trigger()
            self.assertEqual(self.w.tabs.count(), 2)
            self.assertEqual(self.w.tabs.tabText(1), "Lokal")
            view = self.w._terminal_views()[0]
            self.assertTrue(self.pump(lambda: "lokal-ok" in "\n".join(view._screen.display)))
            self.assertIn("a_local", self.w.side_buttons)
            self.assertEqual(self.w.a_local.shortcut().toString(), "Ctrl+Shift+T")
            view._send(b"\r")
            self.assertTrue(self.pump(lambda: self.w.tabs.count() == 1))

        def test_local_terminal_shortcut_inside_a_terminal_opens_a_tile(self):
            from unittest import mock
            from sesam import localshell

            self.w._open_connection(self.conn)
            first = self.w._terminal_views()[0]
            with mock.patch.object(localshell, "command", lambda: ["bash", "-c", "read x"]):
                first.local_requested.emit()
            views = self.w._terminal_views()
            self.assertEqual(len(views), 2)
            self.assertEqual(self.w.tabs.count(), 2)  # Kachel im selben Reiter
            self.assertIs(self.w._tab_of(views[0]), self.w._tab_of(views[1]))

        def test_split_adds_a_tile_in_the_same_tab(self):
            first, second = self._two_tiles()
            self.assertEqual(self.w.tabs.count(), 2)  # kein zweiter Reiter
            self.assertIs(self.w._tab_of(first), self.w._tab_of(second))
            self.assertEqual(self.w.tabs.tabText(1), "Test (+1)")
            self.assertTrue(first._tiled and second._tiled)
            self.assertEqual(second.command, first.command)

        def test_split_orientation(self):
            from PySide6.QtCore import Qt
            from PySide6.QtWidgets import QSplitter

            first, second = self._two_tiles(Qt.Orientation.Vertical)
            self.assertEqual(first.parentWidget().orientation(), Qt.Orientation.Vertical)
            old_parent = second.parentWidget()
            self.w._split_view(second, Qt.Orientation.Horizontal)  # andere Richtung: verschachtelter Splitter
            third = [v for v in self.w._terminal_views() if v not in (first, second)][0]
            self.assertIsInstance(third.parentWidget(), QSplitter)
            self.assertEqual(third.parentWidget().orientation(), Qt.Orientation.Horizontal)
            self.assertIsNot(third.parentWidget(), old_parent)
            self.assertEqual(len(self.w._terminal_views()), 3)

        def test_ending_one_tile_keeps_the_tab_until_the_last_one(self):
            first, second = self._two_tiles()
            second._send(b"\r")  # read x -> Prozess endet
            self.assertTrue(self.pump(lambda: len(self.w._terminal_views()) == 1))
            self.assertEqual(self.w.tabs.count(), 2)
            self.assertEqual(self.w.tabs.tabText(1), "Test")
            self.assertFalse(first._tiled)
            first._send(b"\r")
            self.assertTrue(self.pump(lambda: self.w.tabs.count() == 1))

        def test_closing_a_tab_with_several_tiles_asks_once(self):
            from unittest import mock
            from PySide6.QtWidgets import QMessageBox

            first, second = self._two_tiles()
            self.assertTrue(self.pump(lambda: second._pty is not None))
            with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes) as ask:
                self.w._close_terminal_tab(1)
            self.assertEqual(ask.call_count, 1)
            self.assertTrue(self.pump(lambda: self.w.tabs.count() == 1))

        def test_focus_neighbor_and_zoom(self):
            from PySide6.QtCore import Qt

            first, second = self._two_tiles(Qt.Orientation.Horizontal)
            self.w.resize(1000, 600)
            self.w.show()
            self.pump(lambda: False, timeout=0.3)
            tab = self.w._tab_of(first)
            self.assertIs(tab.neighbor(first, "right"), second)
            self.assertIs(tab.neighbor(second, "left"), first)
            self.assertIsNone(tab.neighbor(first, "up"))
            tab.toggle_zoom(second)
            self.assertIs(tab.zoomed(), second)
            self.assertTrue(first.isHidden())
            tab.toggle_zoom(second)
            self.assertIsNone(tab.zoomed())
            self.assertFalse(first.isHidden())

        def test_tiled_open_goes_next_to_the_last_terminal_tab(self):
            from PySide6.QtCore import Qt

            self.assertIsNone(self.w._last_terminal_tab())
            self.w._open_connection(self.conn)
            first = self.w._terminal_views()[0]
            self.w.tabs.setCurrentIndex(0)  # zurück zur Verbindungsliste
            self.assertIsNotNone(self.w._last_terminal_tab())
            self.w._split_target = (self.w._last_terminal_tab().active(), Qt.Orientation.Vertical)
            self.w._open_connection(self.conn)
            self.assertEqual(self.w.tabs.count(), 2)
            self.assertEqual(len(self.w._terminal_views()), 2)
            self.assertIs(self.w.tabs.currentWidget(), self.w._tab_of(first))

        def _open_sessions(self, n):
            for _ in range(n):
                self.w._open_connection(self.conn)
            views = self.w._terminal_views()
            self.assertEqual(len(views), n)
            self.assertTrue(self.pump(lambda: all(v._pty is not None for v in views)))
            return views

        def test_grid_shape(self):
            from sesam.gui.terminal_tab import TerminalTab

            self.assertEqual([TerminalTab.grid_shape(n) for n in (1, 2, 3, 4, 5, 6)],
                             [(1, 1), (2, 1), (2, 2), (2, 2), (3, 2), (3, 2)])

        def test_tile_all_collects_every_session_into_one_tab(self):
            from PySide6.QtCore import Qt

            self._open_sessions(3)
            self.assertEqual(self.w.tabs.count(), 4)
            self.w._tile_all()
            self.assertEqual(self.w.tabs.count(), 2)
            tab = self.w.tabs.widget(1)
            self.assertEqual(len(tab.views()), 3)
            self.assertEqual(len(self.w._terminal_views()), 3)
            self.assertTrue(all(v._tiled for v in tab.views()))
            self.assertIs(self.w.tabs.currentWidget(), tab)
            self.assertEqual(tab._root.orientation(), Qt.Orientation.Vertical)  # 2 × 2: zwei Zeilen
            self.assertEqual(self.w.tabs.tabText(1), "Test (+2)")

        def test_untile_gives_every_tile_its_own_tab_again(self):
            self._open_sessions(3)
            self.w._tile_all()
            self.w._untile(self.w.tabs.widget(1))
            self.assertEqual(self.w.tabs.count(), 4)
            self.assertTrue(all(len(t.views()) == 1 for t in self.w._terminal_tabs()))
            self.assertFalse(any(v._tiled for v in self.w._terminal_views()))

        def test_tile_all_with_one_session_does_nothing(self):
            self._open_sessions(1)
            self.w._tile_all()
            self.assertEqual(self.w.tabs.count(), 2)

        def test_broadcast_is_off_by_default_and_needs_two_tiles(self):
            first, second = self._two_tiles()
            tab = self.w._tab_of(first)
            self.assertFalse(tab.broadcast())
            first.typed.emit(b"\r")  # ohne Broadcast bleibt die zweite Sitzung unberührt
            self.pump(lambda: False, timeout=0.5)
            self.assertEqual(len(self.w._terminal_views()), 2)
            self.assertIsNone(second.poll())
            second.terminate(force=True)
            self.assertTrue(self.pump(lambda: len(self.w._terminal_views()) == 1))
            self.w._toggle_broadcast(tab)  # allein: nichts zu verteilen
            self.assertFalse(tab.broadcast())

        def test_broadcast_sends_typing_to_the_other_tiles(self):
            first, second = self._two_tiles()
            tab = self.w._tab_of(first)
            self.w._toggle_broadcast(tab)
            self.assertTrue(tab.broadcast())
            self.assertTrue(first.is_broadcasting() and second.is_broadcasting())
            self.assertTrue(self.w.tabs.tabText(1).startswith("⚠"))
            first.typed.emit(b"\r")  # Enter in der ersten Kachel erreicht auch die zweite: ihr „read“ endet
            self.assertTrue(self.pump(lambda: len(self.w._terminal_views()) == 1))
            self.assertIsNone(first.poll())
            self.assertFalse(tab.broadcast())  # mit einer Kachel schaltet sich Broadcast ab
            self.assertFalse(first.is_broadcasting())

        def test_broadcast_paste_asks_before_multiline_text(self):
            from unittest import mock
            from sesam.gui import main_window

            first, second = self._two_tiles()
            self.w._toggle_broadcast(self.w._tab_of(first))
            with mock.patch.object(second, "paste_text") as paste, mock.patch.object(main_window, "confirm_paste", return_value=False) as ask:
                first.pasted.emit("a\nb\n")
            ask.assert_called_once()
            paste.assert_not_called()
            with mock.patch.object(second, "paste_text") as paste, mock.patch.object(main_window, "confirm_paste", return_value=True):
                first.pasted.emit("ls")
            paste.assert_called_once_with("ls")

        def test_context_menu_of_a_connection_still_works_after_a_terminal_tab_was_closed(self):
            from unittest import mock
            from PySide6.QtCore import QEvent
            from PySide6.QtWidgets import QApplication

            self.w._open_connection(self.conn)
            view = self.w._terminal_views()[0]
            self.assertTrue(self.pump(lambda: view._pty is not None))
            self.assertIsNotNone(self.w._last_terminal_tab())
            self.w.tabs.setCurrentIndex(0)
            view._send(b"\r")  # Sitzung endet, der Reiter wird gelöscht
            self.assertTrue(self.pump(lambda: self.w.tabs.count() == 1))
            QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            self.assertIsNone(self.w._last_terminal_tab())  # kein Zugriff auf das gelöschte Objekt
            shown = []
            from sesam.gui import main_window

            self.w.tree.setCurrentItem(self.w._find_item(self.conn.id))
            item = self.w.tree.currentItem()
            with mock.patch.object(main_window.MainWindow, "_popup", lambda self, menu, pos: shown.extend(a.text() for a in menu.actions())):
                self.w._context_menu(self.w.tree.visualItemRect(item).center())
            self.assertIn("Neben dem aktuellen Terminal öffnen", shown)

        # --- Kacheln per Ziehen und Ablegen verschieben ---
        def _order(self, tab):
            return [v.title for v in tab.views()]

        def _three_tiles(self):
            from PySide6.QtCore import Qt

            first, second = self._two_tiles()
            self.w._split_view(second, Qt.Orientation.Horizontal)
            views = self.w._terminal_views()
            third = [v for v in views if v not in (first, second)][0]
            self.assertTrue(self.pump(lambda: third._pty is not None))
            for i, v in enumerate((first, second, third)):
                v.title = f"K{i + 1}"
            return first, second, third

        def test_zone_depends_on_the_edge_next_to_the_drop_point(self):
            from PySide6.QtCore import QPointF

            first, _second = self._two_tiles()
            w, h = first.width(), first.height()
            self.assertEqual(first.zone_at(QPointF(5, h / 2)), "left")
            self.assertEqual(first.zone_at(QPointF(w - 5, h / 2)), "right")
            self.assertEqual(first.zone_at(QPointF(w / 2, 5)), "up")
            self.assertEqual(first.zone_at(QPointF(w / 2, h - 5)), "down")
            self.assertEqual(first.zone_at(QPointF(w / 2, h / 2)), "swap")

        def test_dropping_on_the_left_moves_the_tile_in_front(self):
            first, second, third = self._three_tiles()
            tab = self.w._tab_of(first)
            self.w._drop_tile(third, first, "left")
            self.assertEqual(self._order(tab), ["K3", "K1", "K2"])
            self.assertEqual(len(self.w._terminal_views()), 3)
            self.assertTrue(all(v._tiled for v in tab.views()))

        def test_dropping_below_nests_in_the_other_direction(self):
            from PySide6.QtCore import Qt
            from PySide6.QtWidgets import QSplitter

            first, second, third = self._three_tiles()
            tab = self.w._tab_of(first)
            self.w._drop_tile(first, third, "down")
            self.assertEqual(self._order(tab), ["K2", "K3", "K1"])
            self.assertIsInstance(first.parentWidget(), QSplitter)
            self.assertEqual(first.parentWidget().orientation(), Qt.Orientation.Vertical)
            self.assertIs(first.parentWidget(), third.parentWidget())

        def test_dropping_in_the_middle_swaps_two_tiles(self):
            first, second, third = self._three_tiles()
            tab = self.w._tab_of(first)
            self.w._drop_tile(first, third, "swap")
            self.assertEqual(self._order(tab), ["K3", "K2", "K1"])
            self.assertTrue(all(v.isVisible() for v in tab.views()))
            self.assertIs(tab.active(), first)

        def test_moving_keeps_sessions_running_and_input_to_all(self):
            first, second, third = self._three_tiles()
            tab = self.w._tab_of(first)
            tab.set_broadcast(True)
            self.w._drop_tile(second, first, "up")
            self.assertTrue(tab.broadcast())
            self.assertTrue(all(v.is_broadcasting() for v in tab.views()))
            self.assertTrue(all(v.poll() is None for v in tab.views()))

        def test_dropping_a_tile_from_another_tab_moves_it_over(self):
            self._open_sessions(2)
            tabs = self.w._terminal_tabs()
            self.assertEqual(len(tabs), 2)
            a, b = tabs[0].views()[0], tabs[1].views()[0]
            self.w._drop_tile(b, a, "right")
            self.assertEqual(self.w.tabs.count(), 2)  # der leere Reiter ist weg
            self.assertEqual(self.w._tab_of(a).views(), [a, b])
            self.assertIs(self.w.tabs.currentWidget(), self.w._tab_of(a))
            self.assertTrue(a._tiled and b._tiled)

        def test_dropping_on_itself_changes_nothing(self):
            first, second = self._two_tiles()
            tab = self.w._tab_of(first)
            before = tab.views()
            self.w._drop_tile(first, first, "left")
            self.assertEqual(tab.views(), before)

        def test_grip_drag_hands_the_tile_to_the_target(self):
            from PySide6.QtCore import QMimeData, QPointF, Qt
            from PySide6.QtGui import QDragMoveEvent, QDropEvent
            from sesam.gui.terminal_view import TILE_MIME, TerminalView

            first, second = self._two_tiles()
            self.assertTrue(first._on_grip(first._grip_rect().center()))
            self.assertFalse(first._on_grip(QPointF(10, 10)))
            mime = QMimeData()
            mime.setData(TILE_MIME, b"x")
            pos = QPointF(5, first.height() / 2)
            received = []
            first.tile_dropped.connect(lambda src, zone: received.append((src, zone)))
            TerminalView._dragged = second
            try:
                move = QDragMoveEvent(pos.toPoint(), Qt.DropAction.MoveAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
                first.dragMoveEvent(move)
                self.assertEqual(first._drop_zone, "left")
                drop = QDropEvent(pos, Qt.DropAction.MoveAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
                first.dropEvent(drop)
            finally:
                TerminalView._dragged = None
            self.assertEqual(received, [(second, "left")])
            self.assertIsNone(first._drop_zone)
            self.assertEqual(self.w._tab_of(first).views(), [second, first])  # per Signal im Hauptfenster umgesetzt
