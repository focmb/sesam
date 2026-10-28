"""Snippets in der Oberfläche: Verwaltung, Ausführung, Kontextmenü, Seitenleiste."""
import unittest
from unittest import mock

from gui_base import GuiTestCase

from sesam import snippets, storage
from sesam.models import Config, Connection, Snippet, Terminal
from ssh_helper import wrapped_ssh


class DialogBase(GuiTestCase):
    def setUp(self):
        super().setUp()
        self.cfg = Config(connections=[Connection("web", "w.example", group="Prod"), Connection("db", "d.example"),
                                       Connection("nas", "n.example")])
        self.cfg.snippets = [Snippet("Disk", command="df -h", category="System"),
                             Snippet("Uptime", command="uptime"),
                             Snippet("Journal", command="journalctl -n 50", category="System/Logs", sudo=True)]
        self.saved = 0
        self.runs = []

    def dialog(self, preselect=None):
        from sesam.gui.snippets_dialog import SnippetsDialog

        d = SnippetsDialog(self.cfg, lambda: setattr(self, "saved", self.saved + 1), lambda s, c: self.runs.append((s.name, [x.name for x in c])),
                           preselect or [])
        self.addCleanup(d.close)
        return d

    def select(self, d, name):
        from PySide6.QtWidgets import QTreeWidgetItemIterator

        it = QTreeWidgetItemIterator(d.tree)
        while it.value():
            if it.value().text(0) == name:
                d.tree.setCurrentItem(it.value())
                return
            it += 1
        self.fail(name)


class SnippetsDialogTests(DialogBase):
    def test_tree_shows_categories_nested_and_uncategorised_snippets(self):
        d = self.dialog()
        top = [d.tree.topLevelItem(i).text(0) for i in range(d.tree.topLevelItemCount())]
        self.assertEqual(top, ["System", "Uptime"])
        system = d.tree.topLevelItem(0)
        self.assertEqual([system.child(i).text(0) for i in range(system.childCount())], ["Logs", "Disk"])
        self.assertEqual(system.child(0).child(0).text(0), "Journal")

    def test_selection_shows_command_and_options(self):
        d = self.dialog()
        self.select(d, "Journal")
        self.assertEqual(d.detail.toPlainText(), "journalctl -n 50")
        self.assertIn("mit sudo", d.info.text())
        self.select(d, "Disk")
        self.assertIn("ohne sudo", d.info.text())

    def test_empty_state(self):
        self.cfg.snippets.clear()
        d = self.dialog()
        self.assertIn("Noch keine Snippets", d.info.text())
        self.assertFalse(d.btn_edit.isEnabled() or d.btn_delete.isEnabled() or d.btn_run.isEnabled())

    def test_run_button_needs_snippet_and_target_and_shows_count(self):
        d = self.dialog(preselect=[])
        self.select(d, "Disk")
        self.assertFalse(d.btn_run.isEnabled())
        d.targets.item(0).setCheckState(__import__("PySide6.QtCore", fromlist=["Qt"]).Qt.CheckState.Checked)
        self.assertTrue(d.btn_run.isEnabled())
        self.assertEqual(d.btn_run.text(), "Ausführen")
        d.btn_all.click()
        self.assertEqual(d.btn_run.text(), "Auf 3 Verbindungen ausführen")
        d.btn_none.click()
        self.assertFalse(d.btn_run.isEnabled())

    def test_preselected_connections_are_checked_and_run(self):
        ids = [c.id for c in self.cfg.connections if c.name in ("db", "nas")]
        d = self.dialog(preselect=ids)
        self.select(d, "Uptime")
        d.btn_run.click()
        self.assertEqual(self.runs, [("Uptime", ["db", "nas"])])

    def test_new_snippet_via_edit_dialog(self):
        from sesam.gui.snippets_dialog import SnippetEditDialog

        def fake(dlg):
            dlg.name.setText("Speicher")
            dlg.category.setCurrentText(" Tools//Memory ")
            dlg.command.setPlainText("free -m\n")
            dlg.sudo.setChecked(True)
            dlg.after.setCurrentIndex(dlg.after.findData("wait"))
            dlg._accept()
            return 1

        d = self.dialog()
        with mock.patch.object(SnippetEditDialog, "exec", fake):
            d.btn_new.click()
        sn = next(s for s in self.cfg.snippets if s.name == "Speicher")
        self.assertEqual((sn.category, sn.command, sn.sudo, sn.after), ("Tools/Memory", "free -m", True, "wait"))
        self.assertEqual(self.saved, 1)
        self.assertEqual(d._current().id, sn.id)

    def test_edit_dialog_requires_name_and_command(self):
        from PySide6.QtWidgets import QMessageBox

        from sesam.gui.snippets_dialog import SnippetEditDialog

        dlg = SnippetEditDialog(self.cfg)
        self.addCleanup(dlg.close)
        shown = []
        with mock.patch.object(QMessageBox, "warning", lambda *a: shown.append(a[2])):
            dlg.name.setText("x")
            dlg._accept()
            dlg.command.setPlainText("  \n ")
            dlg._accept()
        self.assertEqual(len(shown), 2)
        self.assertEqual(dlg.snippet.name, "")  # unverändert
        self.assertIn("System", [dlg.category.itemText(i) for i in range(dlg.category.count())])

    def test_edit_existing_and_cancel(self):
        from sesam.gui.snippets_dialog import SnippetEditDialog

        d = self.dialog()
        self.select(d, "Disk")
        with mock.patch.object(SnippetEditDialog, "exec", lambda dlg: (dlg.command.setPlainText("df -hT"), dlg._accept(), 1)[-1]):
            d.btn_edit.click()
        self.assertEqual(self.cfg.snippets[0].command, "df -hT")
        self.assertEqual(self.saved, 1)
        with mock.patch.object(SnippetEditDialog, "exec", lambda dlg: 0):
            d.btn_edit.click()
        self.assertEqual(self.saved, 1)

    def test_duplicate_and_delete(self):
        from PySide6.QtWidgets import QMessageBox

        d = self.dialog()
        self.select(d, "Disk")
        d.btn_dup.click()
        names = sorted(s.name for s in self.cfg.snippets)
        self.assertIn("Disk (Kopie)", names)
        ids = {s.id for s in self.cfg.snippets}
        self.assertEqual(len(ids), 4)
        with mock.patch.object(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.No):
            d.btn_delete.click()
        self.assertEqual(len(self.cfg.snippets), 4)
        with mock.patch.object(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Yes):
            d.btn_delete.click()
        self.assertEqual(len(self.cfg.snippets), 3)


class MainWindowSnippetTests(GuiTestCase):
    def setUp(self):
        super().setUp()
        self.conns = [Connection(n, f"{n}.example") for n in ("a", "b", "c")]
        cfg = Config(connections=self.conns, terminals=[Terminal("T", "xterm -e {ssh_args}")],
                     snippets=[Snippet("Disk", command="df -h", category="System"), Snippet("Up", command="uptime")])
        cfg.default_terminal_id = cfg.terminals[0].id
        storage.save(cfg, self.path)
        self.w = self._window()
        self.addCleanup(self.w.close)
        self.w.tree.expandAll()
        self.popen = mock.patch.object(snippets.launcher.subprocess, "Popen")
        self.mock_popen = self.popen.start()
        self.addCleanup(self.popen.stop)
        patcher = mock.patch.object(snippets.launcher.shutil, "which", lambda n: "/usr/bin/" + n)
        patcher.start()
        self.addCleanup(patcher.stop)

    def item(self, name):
        from PySide6.QtWidgets import QTreeWidgetItemIterator

        it = QTreeWidgetItemIterator(self.w.tree)
        while it.value():
            if it.value().text(0) == name:
                return it.value()
            it += 1
        self.fail(name)

    def menu_texts(self, conns_names, inspect=None):
        from sesam.gui import main_window

        self.w.tree.clearSelection()
        for n in conns_names:
            self.item(n).setSelected(True)
        self.w.tree.setCurrentItem(self.item(conns_names[-1]), 0, __import__("PySide6.QtCore", fromlist=["QItemSelectionModel"]).QItemSelectionModel.SelectionFlag.NoUpdate)
        captured = {}

        def grab(win, menu, pos):
            captured["menu"] = menu
            captured["texts"] = [a.text() for a in menu.actions()]
            if inspect:
                inspect(menu)  # solange das Menü noch lebt

        with mock.patch.object(main_window.MainWindow, "_popup", grab):
            self.w._context_menu(self.w.tree.visualItemRect(self.item(conns_names[0])).center())
        return captured

    def test_sidebar_has_snippets_button_with_icon_and_shortcut(self):
        self.assertEqual(self.w.a_snippets.text(), "Snippets …")
        self.assertIn("a_snippets", self.w.side_buttons)
        self.assertFalse(self.w.a_snippets.icon().isNull())
        self.assertEqual(self.w.a_snippets.shortcut().toString(), "Ctrl+Shift+S")

    @staticmethod
    def snippet_submenu(menu):
        return next(a for a in menu.actions() if a.text() == "Snippet ausführen").menu()

    def test_context_menu_of_one_connection_offers_snippets_by_category(self):
        seen = {}

        def inspect(menu):
            sub = self.snippet_submenu(menu)
            seen["top"] = [a.text() for a in sub.actions()]
            seen["system"] = [a.text() for a in next(a for a in sub.actions() if a.text() == "System").menu().actions()]

        cap = self.menu_texts(["a"], inspect)
        self.assertIn("Snippet ausführen", cap["texts"])
        self.assertEqual(seen["top"], ["System", "Up"])
        self.assertEqual(seen["system"], ["Disk"])

    def test_clicking_a_snippet_in_menu_opens_one_terminal(self):
        self.menu_texts(["b"], lambda menu: next(a for a in self.snippet_submenu(menu).actions() if a.text() == "Up").trigger())
        self.assertEqual(self.mock_popen.call_count, 1)
        ssh = wrapped_ssh(self.mock_popen.call_args.args[0], self.conns[1])  # läuft über den Wrapper
        self.assertEqual(ssh[-1], "sh -c uptime; exec \"${SHELL:-sh}\" -l")
        self.assertIn("b.example", ssh)

    def test_menu_for_several_selected_connections_runs_on_all_after_confirmation(self):
        from PySide6.QtWidgets import QMessageBox

        asked = []

        def trigger(menu):
            up = next(a for a in self.snippet_submenu(menu).actions() if a.text() == "Up")
            with mock.patch.object(QMessageBox, "question", lambda *x: asked.append(x[2]) or QMessageBox.StandardButton.No):
                up.trigger()
            self.assertEqual(self.mock_popen.call_count, 0)
            with mock.patch.object(QMessageBox, "question", lambda *x: QMessageBox.StandardButton.Yes):
                up.trigger()

        cap = self.menu_texts(["a", "c"], trigger)
        self.assertIn("Snippet ausführen", cap["texts"])
        self.assertIn("2 Verbindungen", asked[0])
        self.assertEqual(self.mock_popen.call_count, 2)

    def test_errors_are_reported_without_stopping_the_rest(self):
        from PySide6.QtWidgets import QMessageBox

        self.w.cfg.connections[0].key_id = "weg"
        warnings = []
        with mock.patch.object(QMessageBox, "question", lambda *x: QMessageBox.StandardButton.Yes), \
                mock.patch.object(QMessageBox, "warning", lambda *x: warnings.append(x[2])):
            self.w.run_snippet(self.w.cfg.snippets[0], self.w.cfg.connections[:2])
        self.assertEqual(self.mock_popen.call_count, 1)
        self.assertEqual(len(warnings), 1)
        self.assertIn(self.w.cfg.connections[0].name, warnings[0])

    def test_menu_is_disabled_without_snippets(self):
        self.w.cfg.snippets.clear()
        seen = {}
        self.menu_texts(["a"], lambda menu: seen.update(enabled=next(a for a in menu.actions() if a.text() == "Snippet ausführen").isEnabled()))
        self.assertIs(seen["enabled"], False)

    def test_sidebar_button_opens_dialog_with_selection_preselected(self):
        from sesam.gui import main_window

        self.w.tree.clearSelection()
        for n in ("a", "b"):
            self.item(n).setSelected(True)
        seen = {}

        def fake(dlg):
            seen["checked"] = sorted(c.name for c in dlg._checked())
            return 0

        with mock.patch.object(main_window.SnippetsDialog, "exec", fake):
            self.w.a_snippets.trigger()
        self.assertEqual(seen["checked"], ["a", "b"])


if __name__ == "__main__":
    unittest.main()
