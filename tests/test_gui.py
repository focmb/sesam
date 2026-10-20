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

    @classmethod
    def tearDownClass(cls):
        cls._close_widgets()

    @staticmethod
    def _close_widgets():
        """Alle Fenster/Dialoge schließen und zerstören, bevor Python beendet wird.

        Sonst räumt Qt sie beim Interpreter-Ende in beliebiger Reihenfolge ab,
        was sporadisch mit einem Segfault (Exit-Code 139) endet.
        """
        from PySide6.QtCore import QCoreApplication, QEvent

        for widget in QApplication.topLevelWidgets():
            widget.close()
            widget.deleteLater()
        QCoreApplication.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        QCoreApplication.processEvents()

    def setUp(self):
        from sesam import passwords

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
        self.addCleanup(self._close_widgets)
        self.path = Path(self.tmp.name) / "c.json"

    def _window(self):
        from sesam import storage
        from sesam.gui.main_window import MainWindow

        return MainWindow(storage.load(self.path), self.path)

    def test_password_lifecycle(self):
        from sesam import storage
        from sesam.gui.connection_dialog import ConnectionDialog

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
        from sesam import storage
        from sesam.gui.terminals_dialog import TerminalsDialog

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
        from sesam import groups as g
        from sesam.gui.main_window import GROUP_ROLE, ID_ROLE
        from sesam.models import Connection

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
        with mock.patch("sesam.gui.main_window.QMessageBox.warning") as warn:
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
        from sesam.gui.connection_dialog import ConnectionDialog
        from sesam.models import Connection

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
        with mock.patch("sesam.gui.main_window.launcher.launch") as launch:
            w.connect_selected(sudo=True)
        used = launch.call_args.args[1]
        self.assertEqual(used.sudo_mode, "login")
        self.assertEqual(plain.sudo_mode, "")  # gespeicherte Verbindung bleibt unverändert

    def test_identities_in_connection_dialog(self):
        from sesam.gui.connection_dialog import ConnectionDialog
        from sesam.gui.identities_dialog import IdentitiesDialog, IdentityEditDialog
        from sesam.models import Connection, Identity

        w = self._window()
        saved = []
        # Neue Identität direkt aus dem Verbindungsdialog anlegen und auswählen
        dlg = ConnectionDialog(w.cfg, save_callback=lambda: saved.append(1))
        self.assertEqual(dlg.identity_combo.count(), 1)
        self.assertTrue(dlg.user.isEnabled())
        with mock.patch("sesam.gui.connection_dialog.IdentityEditDialog") as edit_cls:
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
        with mock.patch("sesam.gui.identities_dialog.QMessageBox.question",
                        return_value=__import__("PySide6.QtWidgets", fromlist=["QMessageBox"]).QMessageBox.StandardButton.Yes):
            ids._delete()
        self.assertEqual(w.cfg.identities, [])
        self.assertEqual(conn.identity_id, "")
        self.assertNotIn(ident.id, self.store)

    def test_identity_edit_dialog_password_semantics(self):
        from sesam.gui.identities_dialog import IdentityEditDialog
        from sesam.models import Identity

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

    def test_column_widths_resizable_and_persisted(self):
        from PySide6.QtWidgets import QHeaderView
        from sesam import storage

        w = self._window()
        header = w.tree.header()
        self.assertEqual(header.sectionResizeMode(0), QHeaderView.ResizeMode.Interactive)
        self.assertEqual(header.sectionResizeMode(1), QHeaderView.ResizeMode.Interactive)
        self.assertEqual([header.sectionSize(0), header.sectionSize(1)], list(w.DEFAULT_WIDTHS[:2]))  # Standardbreiten
        header.resizeSection(0, 333)
        header.resizeSection(1, 222)
        self.assertTrue(w._width_timer.isActive())  # Speichern erfolgt verzögert
        w._flush_widths()
        saved = storage.load(self.path).column_widths
        self.assertEqual(saved[:2], [333, 222])

        w2 = self._window()  # neu geöffnet: gespeicherte Breiten werden angewendet
        self.assertEqual([w2.tree.header().sectionSize(0), w2.tree.header().sectionSize(1)], [333, 222])

    def test_sync_controller_end_to_end_and_window_integration(self):
        import subprocess
        from PySide6.QtCore import QEventLoop, QTimer
        from sesam import storage
        from sesam.gui.main_window import MainWindow
        from sesam.gui.sync_controller import SyncController
        from sesam.models import Config, Connection, SyncSettings
        from sesam.syncing import SyncEngine

        class Store:
            d = {}
            get = lambda self, k: self.d.get(k)
            set = lambda self, k, v: self.d.__setitem__(k, v)
            delete = lambda self, k: self.d.pop(k, None)

        remote = Path(self.tmp.name) / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)

        def controller(name):
            cfg = Config(sync=SyncSettings(enabled=True, url=str(remote), passwords=False))
            work = Path(self.tmp.name) / name
            c = SyncController(cfg, lambda: None, engine_factory=lambda: SyncEngine(cfg.sync, work, None, Store(), device=name))
            return cfg, c

        def run(c, manual=True):
            loop = QEventLoop()
            out = {}
            c.finished.connect(lambda r, e, m: (out.update(report=r, error=e, manual=m), loop.quit()))
            QTimer.singleShot(60000, loop.quit)
            c.sync(manual=manual)
            loop.exec()
            return out

        cfgA, A = controller("A")
        cfgA.connections.append(Connection("server", "h.example"))
        out = run(A)
        self.assertIsNone(out["error"])
        self.assertTrue(out["report"].pushed)
        self.assertTrue(out["manual"])
        self.assertTrue(cfgA.sync.last_sync)

        cfgB, B = controller("B")
        changed = []
        B.cfg_changed.connect(lambda: changed.append(1))
        statuses = []
        B.status.connect(lambda t, e: statuses.append((t, e)))
        out = run(B, manual=False)
        self.assertIsNone(out["error"])
        self.assertEqual([c.name for c in cfgB.connections], ["server"])
        self.assertEqual(changed, [1])
        self.assertEqual(statuses[0], ("Synchronisiere …", False))
        self.assertIn("Synchronisiert", statuses[-1][0])

        # Fehlerfall wird gemeldet statt zu hängen
        cfgB.sync.url = str(Path(self.tmp.name) / "gibt-es-nicht.git")
        out = run(B)
        self.assertIsNotNone(out["error"])
        self.assertTrue(statuses[-1][1])
        self.assertFalse(B.busy)

        # Fenster: Änderungen lösen im Automatikmodus verzögert einen Abgleich aus, ohne Aktivierung nicht
        w = self._window()
        w._save()
        self.assertFalse(w.sync.debounce.isActive())
        w.cfg.sync = SyncSettings(enabled=True, url=str(remote), passwords=False, auto=True)
        w._save()
        self.assertTrue(w.sync.debounce.isActive())
        w.sync.debounce.stop()
        w.cfg.sync.auto = False
        w._save()
        self.assertFalse(w.sync.debounce.isActive())

    def test_sync_dialog_validation(self):
        from sesam.gui.sync_dialog import SyncDialog

        w = self._window()
        cfg = w.cfg
        dlg = SyncDialog(cfg)
        self.assertFalse(dlg.url.isEnabled())            # deaktiviert -> Felder gesperrt
        dlg.enabled.setChecked(True)
        self.assertTrue(dlg.url.isEnabled() and dlg.phrase.isEnabled())
        with mock.patch("sesam.gui.sync_dialog.QMessageBox.warning") as warn:
            dlg._accept()                                 # URL fehlt
            dlg.url.setText("git@host:r.git")
            dlg._accept()                                 # Sync-Passwort fehlt
            dlg.phrase.setText("kurz")
            dlg._accept()                                 # zu kurz
            dlg.phrase.setText("langgenug1")
            dlg.phrase2.setText("anders123")
            dlg._accept()                                 # Wiederholung weicht ab
        self.assertEqual(warn.call_count, 4)
        self.assertFalse(cfg.sync.enabled)
        dlg.phrase2.setText("langgenug1")
        dlg._accept()
        self.assertTrue(cfg.sync.enabled and cfg.sync.passphrase_saved)
        self.assertEqual(cfg.sync.url, "git@host:r.git")
        from sesam import syncing
        self.assertEqual(self.store[syncing.PASSPHRASE_KEY], "langgenug1")
        # Ohne Passwort-Sync braucht es kein Sync-Passwort
        cfg2 = type(cfg)()
        d2 = SyncDialog(cfg2)
        d2.enabled.setChecked(True)
        d2.url.setText("x")
        d2.passwords.setChecked(False)
        d2._accept()
        self.assertTrue(cfg2.sync.enabled and not cfg2.sync.passwords)


if __name__ == "__main__":
    unittest.main()
