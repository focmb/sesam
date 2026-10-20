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

    def test_group_tree_and_moves(self):
        from PySide6.QtCore import QMimeData, QPointF, Qt
        from PySide6.QtGui import QDropEvent
        from ssh_verwaltung import groups as g
        from ssh_verwaltung.gui.main_window import GROUP_ROLE, ID_ROLE
        from ssh_verwaltung.models import Connection

        w = self._window()
        cfg = w.cfg
        cfg.connections += [Connection("a", "h", group="K/Prod"), Connection("b", "h"), Connection("c", "h", group="K")]
        cfg.groups = ["Leer"]
        w.refresh()

        def find(path):
            stack = [w.tree.topLevelItem(i) for i in range(w.tree.topLevelItemCount())]
            while stack:
                it = stack.pop()
                if it.data(0, GROUP_ROLE) == path:
                    return it
                stack += [it.child(i) for i in range(it.childCount())]

        top = [w.tree.topLevelItem(i).text(0) for i in range(w.tree.topLevelItemCount())]
        self.assertEqual(top, ["K", "Leer", "b"])  # Gruppen zuerst, dann Verbindungen
        self.assertEqual(find("K/Prod").parent(), find("K"))
        self.assertEqual(find("Leer").text(1), "leer")

        # Verbindung per Methode in Untergruppe verschieben
        b = next(c for c in cfg.connections if c.name == "b")
        w._move("conn", b.id, "K/Prod")
        self.assertEqual(b.group, "K/Prod")
        # Gruppe in Gruppe verschieben, Zyklus wird abgelehnt
        w._move("group", "Leer", "K")
        self.assertIn("K/Leer", g.all_groups(cfg))
        with mock.patch("ssh_verwaltung.gui.main_window.QMessageBox.warning") as warn:
            w._move("group", "K", "K/Prod")
        warn.assert_called_once()

        # Echtes Drop-Ereignis: Verbindung "c" auf Gruppe "K/Prod" ziehen
        w.show()
        w.refresh()
        c = next(x for x in cfg.connections if x.name == "c")
        item_c = next(
            it for it in (find("K").child(i) for i in range(find("K").childCount())) if it.data(0, ID_ROLE) == c.id
        )
        w.tree.setCurrentItem(item_c)
        target = find("K/Prod")
        pos = w.tree.visualItemRect(target).center()
        got = []
        w.tree.moved.disconnect()
        w.tree.moved.connect(lambda *a: got.append(a))
        ev = QDropEvent(QPointF(pos), Qt.DropAction.MoveAction, QMimeData(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        w.tree.dropEvent(ev)
        self.assertEqual(got, [("conn", c.id, "K/Prod")])
        # Drop auf leere Fläche = oberste Ebene
        w.tree.setCurrentItem(item_c)
        ev = QDropEvent(QPointF(5, w.tree.viewport().height() - 2), Qt.DropAction.MoveAction, QMimeData(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        w.tree.dropEvent(ev)
        self.assertEqual(got[-1], ("conn", c.id, ""))

        # Suche blendet nicht passende Gruppen aus
        w.search.setText("zzz")
        self.assertEqual(w.tree.topLevelItemCount(), 0)
        w.search.setText("a")
        self.assertTrue(find("K/Prod") is not None)

    def test_sudo_dialog_and_oneoff_action(self):
        from ssh_verwaltung.gui.connection_dialog import ConnectionDialog
        from ssh_verwaltung.models import Connection

        w = self._window()
        dlg = ConnectionDialog(w.cfg)
        dlg.name.setText("S")
        dlg.host.setText("h")
        self.assertFalse(dlg.sudo_user.isEnabled())
        self.assertFalse(dlg.sudo_pw.isEnabled())
        dlg.sudo_mode.setCurrentIndex(dlg.sudo_mode.findData("shell"))
        self.assertTrue(dlg.sudo_user.isEnabled())
        self.assertTrue(dlg.sudo_pw.isEnabled() and dlg.sudo_pw.isChecked())  # Standard: Passwort für sudo nutzen
        dlg.sudo_user.setText("app")
        dlg.sudo_pw.setChecked(False)
        dlg._accept()
        c = dlg.result_connection()
        self.assertEqual((c.sudo_mode, c.sudo_user, c.sudo_use_password), ("shell", "app", False))

        # Einmalige Aktion "Als root verbinden" überschreibt nur die Kopie
        plain = Connection("P", "h2")
        w.cfg.connections += [plain]
        w.refresh()
        item = next(w.tree.topLevelItem(i) for i in range(w.tree.topLevelItemCount()) if w.tree.topLevelItem(i).text(0) == "P")
        w.tree.setCurrentItem(item)
        with mock.patch("ssh_verwaltung.gui.main_window.launcher.launch") as launch:
            w.connect_selected(sudo=True)
        used = launch.call_args.args[1]
        self.assertEqual(used.sudo_mode, "login")
        self.assertEqual(plain.sudo_mode, "")  # gespeicherte Verbindung bleibt unverändert

    def test_identities_in_connection_dialog(self):
        from ssh_verwaltung.gui.connection_dialog import ConnectionDialog
        from ssh_verwaltung.gui.identities_dialog import IdentitiesDialog, IdentityEditDialog
        from ssh_verwaltung.models import Connection, Identity

        w = self._window()
        saved = []
        # Neue Identität direkt aus dem Verbindungsdialog anlegen und auswählen
        dlg = ConnectionDialog(w.cfg, save_callback=lambda: saved.append(1))
        self.assertEqual(dlg.identity_combo.count(), 1)
        self.assertTrue(dlg.user.isEnabled())
        with mock.patch("ssh_verwaltung.gui.connection_dialog.IdentityEditDialog") as edit_cls:
            ident = Identity("deploy", user="deploy", identity_file="/k")
            inst = edit_cls.return_value
            inst.exec.return_value = True
            inst.ident = ident
            inst.password_change = "pw-deploy"
            dlg._new_identity()
        self.assertEqual(w.cfg.identities, [ident])
        self.assertTrue(ident.use_password and self.store[ident.id] == "pw-deploy")
        self.assertEqual(saved, [1])
        self.assertEqual(dlg.identity_combo.currentData(), ident.id)
        for field in (dlg.user, dlg.identity, dlg.password):
            self.assertFalse(field.isEnabled())
        self.assertIn("deploy", dlg.identity_info.text())

        dlg.name.setText("S"); dlg.host.setText("h")
        dlg._accept()
        conn = dlg.result_connection()
        self.assertEqual(conn.identity_id, ident.id)
        self.assertFalse(conn.use_password)  # eigenes Passwort der Verbindung bleibt leer
        w.cfg.connections.append(conn)
        w.refresh()
        self.assertEqual(w.tree.topLevelItem(0).text(1), "deploy@h")  # Ziel zeigt Benutzer der Identität

        # Bestehende Verbindung zeigt die gewählte Identität; zurück auf "Keine" aktiviert die Felder
        d2 = ConnectionDialog(w.cfg, conn)
        self.assertEqual(d2.identity_combo.currentData(), ident.id)
        d2.identity_combo.setCurrentIndex(0)
        self.assertTrue(d2.user.isEnabled())

        # Identität löschen räumt Verbindungen und Schlüsselbund auf
        ids = IdentitiesDialog(w.cfg, lambda: None)
        ids.list.setCurrentRow(0)
        with mock.patch("ssh_verwaltung.gui.identities_dialog.QMessageBox.question",
                        return_value=__import__("PySide6.QtWidgets", fromlist=["QMessageBox"]).QMessageBox.StandardButton.Yes):
            ids._delete()
        self.assertEqual(w.cfg.identities, [])
        self.assertEqual(conn.identity_id, "")
        self.assertNotIn(ident.id, self.store)

    def test_identity_edit_dialog_password_semantics(self):
        from ssh_verwaltung.gui.identities_dialog import IdentityEditDialog
        from ssh_verwaltung.models import Identity

        d = IdentityEditDialog()
        d.name.setText("x")
        d.password.setText("a")
        d._accept()
        self.assertEqual(d.password_change, "a")
        i = Identity("y", use_password=True)
        d = IdentityEditDialog(i)
        d.clear_password.setChecked(True)
        d._accept()
        self.assertEqual(d.password_change, "")
        d = IdentityEditDialog(i)
        d._accept()
        self.assertIsNone(d.password_change)


if __name__ == "__main__":
    unittest.main()
