"""Zwischenablage-Verlauf: Aufnahme, Grenzen, Anheften, Leeren und die verschlüsselte Speicherung (ohne Oberfläche)."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

from sesam import clipboard_history as ch


class FakeKeys(ch.KeyStore):
    def __init__(self, available=True):
        self.available = available
        self.key = os.urandom(32) if available else None
        self.created = 0

    def get(self):
        return self.key

    def create(self):
        if not self.available:
            return None
        self.created += 1
        self.key = os.urandom(32)
        return self.key


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "sub" / "clipboard.json"
        self.keys = FakeKeys()
        self.h = ch.History(self.path, self.keys)

    def reload(self, keys=None):
        other = ch.History(self.path, keys or self.keys)
        other.load()
        return other

    # --- Aufnahme ---
    def test_add_puts_newest_first_and_moves_duplicates_up(self):
        self.h.add("eins")
        self.h.add("zwei")
        self.h.add("eins", source="Server")
        self.assertEqual([e.text for e in self.h.entries], ["eins", "zwei"])
        self.assertEqual(self.h.entries[0].source, "Server")

    def test_empty_blank_and_oversized_text_is_rejected(self):
        for text in ("", "   \n\t", "x" * (ch.MAX_ENTRY_BYTES + 1), "ä" * (ch.MAX_ENTRY_BYTES // 2 + 1)):
            self.assertIsNone(self.h.add(text))
        self.assertEqual(self.h.entries, [])
        self.assertIsNotNone(self.h.add("x" * ch.MAX_ENTRY_BYTES))  # genau die Grenze geht

    def test_disabled_history_records_nothing(self):
        self.h.settings.enabled = False
        self.assertIsNone(self.h.add("text"))

    def test_entry_count_limit_drops_oldest_unpinned_first(self):
        first = self.h.add("pinned")
        self.h.set_pinned(first.id, True)
        for i in range(ch.MAX_ENTRIES + 5):
            self.h.add(f"e{i}")
        self.assertEqual(len(self.h.entries), ch.MAX_ENTRIES)
        self.assertIn("pinned", [e.text for e in self.h.entries])
        self.assertNotIn("e0", [e.text for e in self.h.entries])
        self.assertIn(f"e{ch.MAX_ENTRIES + 4}", [e.text for e in self.h.entries])

    def test_total_size_limit(self):
        chunk = "a" * 900_000
        for i in range(30):
            self.h.add(f"{i}{chunk}")
        self.assertLessEqual(sum(e.size for e in self.h.entries), ch.MAX_TOTAL_BYTES)
        self.assertTrue(self.h.entries[0].text.startswith("29"))

    def test_long_text_properties(self):
        e = self.h.add("\n\n  erste Zeile  \nzweite\n")
        self.assertEqual(e.first_line(), "erste Zeile")
        self.assertEqual(e.lines, 4)
        self.assertEqual(ch.human_size(512), "512 B")
        self.assertEqual(ch.human_size(2048), "2.0 KB")

    # --- Reihenfolge, Suche, Anheften ---
    def test_pinned_come_first_in_pin_order(self):
        a, b, c = self.h.add("a"), self.h.add("b"), self.h.add("c")
        self.h.set_pinned(b.id, True)
        self.h.set_pinned(a.id, True)
        self.assertEqual([e.text for e in self.h.ordered()], ["b", "a", "c"])
        self.h.set_pinned(b.id, False)
        self.assertEqual([e.text for e in self.h.ordered()], ["a", "c", "b"])

    def test_search_matches_all_words_in_text_or_source(self):
        self.h.add("docker compose up -d", source="Web")
        self.h.add("ls -la")
        self.assertEqual([e.text for e in self.h.search("DOCKER up")], ["docker compose up -d"])
        self.assertEqual([e.text for e in self.h.search("web")], ["docker compose up -d"])
        self.assertEqual(self.h.search("gibt es nicht"), [])
        self.assertEqual(len(self.h.search("")), 2)

    # --- Leeren ---
    def test_clear_keeps_pinned_unless_asked(self):
        a = self.h.add("a")
        self.h.add("b")
        self.h.set_pinned(a.id, True)
        self.h.clear()
        self.assertEqual([e.text for e in self.h.entries], ["a"])
        self.h.clear(include_pinned=True)
        self.assertEqual(self.h.entries, [])

    def test_clear_all_renews_the_key_so_old_file_contents_are_unreadable(self):
        self.h.add("geheim")
        self.assertTrue(self.h.save())
        old_file = self.path.read_text(encoding="utf-8")
        old_key = self.keys.key
        self.h.clear(include_pinned=True)
        self.assertTrue(self.h.save())
        self.assertNotEqual(self.keys.key, old_key)
        with self.assertRaises(Exception):
            self.h._decrypt(self.keys.key, json.loads(old_file)["data"])
        self.assertEqual(self.reload().entries, [])

    def test_listeners_are_called(self):
        calls = []
        self.h.listeners.append(lambda: calls.append(1))
        e = self.h.add("x")
        self.h.set_pinned(e.id, True)
        self.h.remove(e.id)
        self.h.clear()
        self.assertEqual(len(calls), 4)

    # --- Speichern und Laden ---
    def test_roundtrip_is_encrypted_and_private(self):
        e = self.h.add("passwort: hunter2", source="Server")
        self.h.set_pinned(e.id, True)
        self.h.add("zweiter Eintrag\nmit Zeilen")
        self.h.settings.clear_on_exit = True
        self.assertTrue(self.h.save())
        raw = self.path.read_text(encoding="utf-8")
        self.assertNotIn("hunter2", raw)  # nicht im Klartext
        self.assertTrue(json.loads(raw)["encrypted"])
        if os.name == "posix":
            self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        loaded = self.reload()
        self.assertEqual([x.text for x in loaded.entries], ["zweiter Eintrag\nmit Zeilen", "passwort: hunter2"])
        self.assertTrue(loaded.get(e.id).pinned)
        self.assertEqual(loaded.get(e.id).source, "Server")
        self.assertTrue(loaded.settings.clear_on_exit)

    def test_without_a_keyring_nothing_is_written_unless_plaintext_is_allowed(self):
        h = ch.History(self.path, FakeKeys(available=False))
        h.add("nur im Speicher")
        self.assertFalse(h.save())
        self.assertTrue(h.notice)
        self.assertNotIn("nur im Speicher", self.path.read_text(encoding="utf-8"))
        h.settings.allow_plaintext = True
        self.assertTrue(h.save())
        self.assertEqual(h.notice, "")
        other = ch.History(self.path, FakeKeys(available=False))
        other.load()
        self.assertEqual([e.text for e in other.entries], ["nur im Speicher"])

    def test_persist_off_writes_no_entries(self):
        self.h.settings.persist = False
        self.h.add("flüchtig")
        self.assertTrue(self.h.save())
        self.assertNotIn("flüchtig", self.path.read_text(encoding="utf-8"))
        self.assertFalse(self.reload().settings.persist)

    def test_locked_file_is_never_overwritten_and_unlock_merges(self):
        self.h.add("alt")
        self.h.save()
        before = self.path.read_text(encoding="utf-8")
        keys = FakeKeys()
        keys.key = None  # Schlüsselbund gesperrt
        locked = ch.History(self.path, keys)
        locked.load()
        self.assertTrue(locked.locked)
        self.assertTrue(locked.notice)
        locked.add("neu")
        self.assertFalse(locked.save())
        self.assertEqual(self.path.read_text(encoding="utf-8"), before)
        keys.key = self.keys.key  # jetzt entsperrt
        self.assertTrue(locked.unlock())
        self.assertEqual({e.text for e in locked.entries}, {"alt", "neu"})
        self.assertTrue(locked.save())

    def test_corrupt_file_is_set_aside_and_start_is_empty(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("{kaputt", encoding="utf-8")
        h = self.reload()
        self.assertEqual(h.entries, [])
        self.assertTrue(h.notice)
        self.assertTrue(self.path.with_name("clipboard.json.defekt").exists())

    def test_wrong_key_is_treated_like_a_corrupt_file(self):
        self.h.add("x")
        self.h.save()
        h = self.reload(FakeKeys())  # anderer Schlüssel
        self.assertEqual(h.entries, [])
        self.assertTrue(self.path.with_name("clipboard.json.defekt").exists())

    def test_garbage_entries_in_a_plaintext_file_are_skipped(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps({"version": 1, "encrypted": False, "settings": {"persist": "ja", "enabled": False},
                                         "entries": [{"text": "gut", "pinned": True}, {"text": 5}, "x", {"text": ""}, {"text": "auch", "created": "gestern"}]}),
                             encoding="utf-8")
        h = self.reload()
        self.assertEqual([e.text for e in h.entries], ["gut", "auch"])
        self.assertTrue(h.entries[0].pinned)
        self.assertTrue(h.settings.persist)  # falscher Typ: Standard bleibt
        self.assertFalse(h.settings.enabled)

    def test_missing_file_is_an_empty_history(self):
        h = self.reload()
        self.assertEqual(h.entries, [])
        self.assertEqual(h.notice, "")


if __name__ == "__main__":
    unittest.main()


# --- Oberfläche -------------------------------------------------------------------------------------------------------

try:
    from gui_base import GuiTestCase
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QCloseEvent, QGuiApplication
    from PySide6.QtWidgets import QMessageBox
    import pyte  # noqa: F401
    HAVE_GUI = True
except ImportError:  # PySide6 oder pyte fehlt
    HAVE_GUI = False

if HAVE_GUI and not sys.platform.startswith("win"):
    import time
    from unittest import mock

    from sesam import launcher, storage, terminals
    from sesam.gui import clipboard_dialog
    from sesam.models import Config, Connection

    class ClipboardGuiTests(GuiTestCase):
        def setUp(self):
            super().setUp()
            cfg = Config(terminals=[terminals.integrated_terminal()], default_terminal_id=terminals.INTEGRATED_ID)
            self.conns = [Connection("Eins", "localhost"), Connection("Zwei", "localhost")]
            cfg.connections.extend(self.conns)
            storage.save(cfg, self.path)
            # statt ssh läuft eine Shell, die jede Eingabezeile mit „got:“ wiederholt
            patcher = mock.patch.object(launcher, "wrapper_args", lambda conn, remote_command=None: ["bash", "-c", "while IFS= read -r l; do echo got:$l; done"])
            patcher.start()
            self.addCleanup(patcher.stop)
            self.w = self._window()
            self.addCleanup(self.w.close)
            self.addCleanup(lambda: [v.terminate(force=True) for v in self.w._terminal_views()])
            self.w.clipboard.keys = FakeKeys()

        def pump(self, until, timeout=5.0):
            end = time.time() + timeout
            while time.time() < end:
                self.app.processEvents()
                if until():
                    return True
                time.sleep(0.01)
            return False

        def open_sessions(self, n=2):
            for conn in self.conns[:n]:
                self.w._open_connection(conn)
            views = self.w._terminal_views()
            self.assertTrue(self.pump(lambda: all(v._pty is not None for v in views)))
            return views

        def text(self, view):
            return "\n".join(line.rstrip() for line in view._screen.display)

        # --- Aufnahme ---
        def test_terminal_copy_is_recorded_once_with_its_source(self):
            (view, _) = self.open_sessions()
            view._sel = ((0, 0), (0, 3))
            view._screen.draw("abcd")  # etwas Sichtbares unter der Auswahl
            with mock.patch.object(type(view), "selected_text", lambda self: "kopierter Text"):
                view.copy()
            self.app.processEvents()
            self.assertEqual([(e.text, e.source) for e in self.w.clipboard.entries], [("kopierter Text", "Eins")])

        def test_system_clipboard_is_read_when_active_and_secrets_are_skipped(self):
            QGuiApplication.clipboard().setText("aus einem anderen Programm")
            self.w._read_system_clipboard()
            self.assertEqual(self.w.clipboard.entries[0].text, "aus einem anderen Programm")
            self.assertEqual(self.w.clipboard.entries[0].source, "")
            from PySide6.QtCore import QMimeData

            mime = QMimeData()
            mime.setText("passwort123")
            mime.setData("x-kde-passwordManagerHint", b"secret")
            QGuiApplication.clipboard().setMimeData(mime)
            self.w._read_system_clipboard()
            self.assertNotIn("passwort123", [e.text for e in self.w.clipboard.entries])

        def test_oversized_text_is_not_recorded(self):
            self.w._record_clip("x" * (ch.MAX_ENTRY_BYTES + 1), "")
            self.assertEqual(self.w.clipboard.entries, [])

        def test_history_is_saved_on_exit_and_loaded_again(self):
            self.w.clipboard.add("bleibt")
            self.w.closeEvent(QCloseEvent())
            other = ch.History(self.path.parent / "clipboard.json", self.w.clipboard.keys)
            other.load()
            self.assertEqual([e.text for e in other.entries], ["bleibt"])

        def test_clear_on_exit_keeps_only_pinned_entries(self):
            a = self.w.clipboard.add("angeheftet")
            self.w.clipboard.add("flüchtig")
            self.w.clipboard.set_pinned(a.id, True)
            self.w.clipboard.settings.clear_on_exit = True
            self.w.closeEvent(QCloseEvent())
            other = ch.History(self.path.parent / "clipboard.json", self.w.clipboard.keys)
            other.load()
            self.assertEqual([e.text for e in other.entries], ["angeheftet"])

        # --- Einfügen ---
        def test_popup_pastes_into_the_active_terminal(self):
            (view, _) = self.open_sessions()
            self.w.clipboard.add("ls -la")
            self.w.clipboard.add("docker ps")
            view.history_requested.emit()
            popup = self.w._clip_popup
            self.assertEqual(popup.list.count(), 2)
            popup.search.setText("ls")
            self.assertEqual(popup.list.count(), 1)
            popup._choose(enter=True)  # einfügen und ausführen
            self.assertTrue(self.pump(lambda: "got:ls -la" in self.text(view)))

        def test_popup_alt_digit_picks_directly_and_pinned_come_first(self):
            (view, _) = self.open_sessions()
            self.w.clipboard.add("erster")
            second = self.w.clipboard.add("zweiter")
            self.w.clipboard.set_pinned(second.id, True)
            popup = clipboard_dialog.ClipboardPopup(self.w.clipboard, self.w)
            picked = []
            popup.chosen.connect(lambda i, e: picked.append((self.w.clipboard.get(i).text, e)))
            self.assertIn("★", popup.list.item(0).text())
            popup._choose_slot(2)
            self.assertEqual(picked, [("erster", False)])

        def test_dialog_pastes_into_all_checked_tabs(self):
            views = self.open_sessions()
            self.w.clipboard.add("gleicher Text")
            dialog = clipboard_dialog.ClipboardDialog(self.w.clipboard, self.w._terminal_sessions, self.w._paste_into, views[0], self.w)
            self.assertEqual(dialog.targets.count(), 2)
            self.assertEqual(len(dialog._checked_views()), 1)  # der aktive Reiter ist vorgewählt
            dialog._check_all(Qt.CheckState.Checked)
            dialog.with_enter.setChecked(True)
            dialog._paste()
            for view in views:
                self.assertTrue(self.pump(lambda v=view: "got:gleicher Text" in self.text(v)), self.text(view))

        def test_dialog_asks_before_pasting_multiline_text_into_several_tabs(self):
            views = self.open_sessions()
            self.w.clipboard.add("a\nb\nc")
            dialog = clipboard_dialog.ClipboardDialog(self.w.clipboard, self.w._terminal_sessions, self.w._paste_into, None, self.w)
            dialog._check_all(Qt.CheckState.Checked)
            with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No) as ask:
                dialog._paste()
            self.assertTrue(ask.called)
            self.assertNotIn("got:a", self.text(views[0]))

        def test_dialog_without_a_ticked_tab_does_not_paste(self):
            self.open_sessions()
            self.w.clipboard.add("x")
            dialog = clipboard_dialog.ClipboardDialog(self.w.clipboard, self.w._terminal_sessions, self.w._paste_into, None, self.w)
            with mock.patch.object(QMessageBox, "information") as info:
                dialog._paste()
            self.assertTrue(info.called)

        # --- Verwalten ---
        def make_dialog(self):
            return clipboard_dialog.ClipboardDialog(self.w.clipboard, self.w._terminal_sessions, self.w._paste_into, None, self.w)

        def test_dialog_pin_delete_new_and_search(self):
            self.w.clipboard.add("alpha")
            self.w.clipboard.add("beta")
            dialog = self.make_dialog()
            dialog.list.setCurrentRow(1)  # alpha
            dialog._toggle_pin()
            self.assertTrue(self.w.clipboard.entries[-1].pinned)
            self.assertEqual(dialog.pin_button.text(), "Lösen")
            self.assertIn("★", dialog.list.item(0).text())  # Angeheftetes steht oben
            with mock.patch.object(clipboard_dialog.ClipboardDialog, "_ask_new_text", return_value="gamma\ndelta"):
                dialog._new()
            self.assertEqual(dialog.list.count(), 3)
            dialog.search.setText("gamma")
            self.assertEqual(dialog.list.count(), 1)
            dialog._delete()
            self.assertNotIn("gamma\ndelta", [e.text for e in self.w.clipboard.entries])

        def test_long_text_preview_is_truncated(self):
            self.w.clipboard.add("z" * (clipboard_dialog.PREVIEW_CHARS + 500))
            dialog = self.make_dialog()
            self.assertLess(len(dialog.preview.toPlainText()), clipboard_dialog.PREVIEW_CHARS + 200)
            self.assertIn(str(clipboard_dialog.PREVIEW_CHARS + 500), dialog.preview.toPlainText())

        def test_clear_all_has_two_levels_and_can_clear_the_system_clipboard(self):
            a = self.w.clipboard.add("angeheftet")
            self.w.clipboard.add("normal")
            self.w.clipboard.set_pinned(a.id, True)
            dialog = self.make_dialog()
            with mock.patch.object(clipboard_dialog.ClipboardDialog, "_ask_clear", return_value=("", False)):
                dialog._clear_all()  # Abbruch
            self.assertEqual(len(self.w.clipboard.entries), 2)
            QGuiApplication.clipboard().setText("system")
            with mock.patch.object(clipboard_dialog.ClipboardDialog, "_ask_clear", return_value=("history", True)):
                dialog._clear_all()
            self.assertEqual([e.text for e in self.w.clipboard.entries], ["angeheftet"])
            self.assertEqual(QGuiApplication.clipboard().text(), "")
            with mock.patch.object(clipboard_dialog.ClipboardDialog, "_ask_clear", return_value=("all", False)):
                dialog._clear_all()
            self.assertEqual(self.w.clipboard.entries, [])
            self.assertEqual(dialog.info.text(), "Der Verlauf ist leer.")

        def test_options_change_the_settings(self):
            dialog = self.make_dialog()
            dialog.opt_exit.setChecked(True)
            dialog.opt_persist.setChecked(False)
            self.assertTrue(self.w.clipboard.settings.clear_on_exit)
            self.assertFalse(self.w.clipboard.settings.persist)

        def test_sidebar_has_the_clipboard_button(self):
            self.assertIn("a_clipboard", self.w.side_buttons)
            self.assertFalse(self.w.a_clipboard.icon().isNull())

        def test_tab_menu_paste_everywhere(self):
            views = self.open_sessions()
            QGuiApplication.clipboard().setText("überall")
            self.w._paste_clipboard_everywhere()
            for view in views:
                view._send(b"\r")
                self.assertTrue(self.pump(lambda v=view: "got:überall" in self.text(v)), self.text(view))
