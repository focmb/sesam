"""Schlüsselverwaltung in der Oberfläche."""
import time
import unittest
from pathlib import Path
from unittest import mock

from gui_base import GuiTestCase

from sesam import keys, keystore, sftp, storage
from sesam.models import Config, Connection, Identity


class KeyGuiBase(GuiTestCase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(keystore, "directory", lambda: Path(self.tmp.name) / "keys")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.saved = 0
        self.cfg = Config()

    def save(self):
        self.saved += 1

    def dialog(self):
        from sesam.gui.keys_dialog import KeysDialog

        d = KeysDialog(self.cfg, self.save)
        self.addCleanup(d.close)
        return d

    def add_key(self, name="Arbeit", passphrase=None):
        m = keys.generate("ed25519", passphrase)
        entry = keys.new_entry(name, m, "me@x")
        keystore.FileStore().set(entry.id, m.private)
        self.cfg.ssh_keys.append(entry)
        return entry, m

    def pump(self, until, timeout=5.0):
        end = time.time() + timeout
        while time.time() < end and not until():
            self.app.processEvents()
            time.sleep(0.01)
        self.assertTrue(until(), "Bedingung nicht rechtzeitig erfüllt")


class KeysDialogTests(KeyGuiBase):
    def test_empty_dialog_disables_actions(self):
        d = self.dialog()
        self.assertFalse(d.btn_delete.isEnabled() or d.btn_copy.isEnabled() or d.btn_install.isEnabled())
        self.assertIn("Noch keine Schlüssel", d.detail.toPlainText())

    def test_shows_details(self):
        entry, m = self.add_key()
        self.cfg.connections.append(Connection("web", "h", key_id=entry.id))
        d = self.dialog()
        text = d.detail.toPlainText()
        self.assertIn(m.fingerprint, text)
        self.assertIn(entry.authorized_line, text)
        self.assertIn("Verwendet von: web", text)
        self.assertIn("auf diesem Gerät: ja", text)

    def test_shows_missing_private_part(self):
        entry, _ = self.add_key()
        keystore.FileStore().delete(entry.id)
        self.assertIn("auf diesem Gerät: nein", self.dialog().detail.toPlainText())

    def test_generate_stores_key_and_saves(self):
        from sesam.gui.keys_dialog import GenerateKeyDialog

        def fake_exec(dlg):
            dlg.name.setText("Neu")
            dlg.comment.setText("c@d")
            dlg._accept()
            return 1

        d = self.dialog()
        with mock.patch.object(GenerateKeyDialog, "exec", fake_exec):
            d._generate()
        (entry,) = self.cfg.ssh_keys
        self.assertEqual((entry.name, entry.type, entry.comment), ("Neu", "ed25519", "c@d"))
        self.assertTrue(keystore.FileStore().exists(entry.id))
        self.assertTrue(keystore.FileStore().get(entry.id).startswith("-----BEGIN OPENSSH PRIVATE KEY-----"))
        self.assertEqual(self.saved, 1)
        self.assertEqual(d.list.count(), 1)
        self.assertIn(entry.fingerprint, d.detail.toPlainText())

    def test_generate_dialog_validates(self):
        from PySide6.QtWidgets import QMessageBox

        from sesam.gui.keys_dialog import GenerateKeyDialog

        dlg = GenerateKeyDialog()
        self.addCleanup(dlg.close)
        warnings = []
        with mock.patch.object(QMessageBox, "warning", lambda *a: warnings.append(a[2])):
            dlg._accept()  # kein Name
            dlg.name.setText("x")
            dlg.passphrase.setText("a")
            dlg.repeat.setText("b")
            dlg._accept()
        self.assertEqual(len(warnings), 2)
        self.assertIn("stimmen nicht überein", warnings[1])
        self.assertIsNone(dlg.material)
        dlg.repeat.setText("a")
        dlg._accept()
        self.assertTrue(dlg.material.encrypted)

    def test_import_unencrypted_file(self):
        from PySide6.QtWidgets import QFileDialog, QInputDialog

        m = keys.generate("ed25519")
        src = Path(self.tmp.name) / "id_work"
        src.write_text(m.private)
        d = self.dialog()
        with mock.patch.object(QFileDialog, "getOpenFileName", lambda *a, **k: (str(src), "")), \
                mock.patch.object(QInputDialog, "getText", lambda *a, **k: ("Importiert", True)):
            d._import()
        (entry,) = self.cfg.ssh_keys
        self.assertEqual((entry.name, entry.fingerprint, entry.encrypted), ("Importiert", m.fingerprint, False))
        self.assertEqual(keystore.FileStore().get(entry.id), m.private)
        self.assertEqual(self.saved, 1)

    def test_import_encrypted_file_without_pub_asks_for_passphrase(self):
        from PySide6.QtWidgets import QFileDialog, QInputDialog

        m = keys.generate("ed25519", "pw")
        src = Path(self.tmp.name) / "id_enc"
        src.write_text(m.private)
        answers = iter([("pw", True), ("Geschützt", True)])
        d = self.dialog()
        with mock.patch.object(QFileDialog, "getOpenFileName", lambda *a, **k: (str(src), "")), \
                mock.patch.object(QInputDialog, "getText", lambda *a, **k: next(answers)):
            d._import()
        (entry,) = self.cfg.ssh_keys
        self.assertTrue(entry.encrypted)
        self.assertEqual(entry.public_key, m.public)

    def test_import_garbage_shows_error_and_changes_nothing(self):
        from PySide6.QtWidgets import QFileDialog, QMessageBox

        src = Path(self.tmp.name) / "müll"
        src.write_text("das ist kein Schlüssel")
        shown = []
        d = self.dialog()
        with mock.patch.object(QFileDialog, "getOpenFileName", lambda *a, **k: (str(src), "")), \
                mock.patch.object(QMessageBox, "critical", lambda *a: shown.append(a[2])):
            d._import()
        self.assertEqual((self.cfg.ssh_keys, self.saved, len(shown)), ([], 0, 1))

    def test_copy_puts_public_line_on_clipboard(self):
        from PySide6.QtGui import QGuiApplication

        entry, _ = self.add_key()
        d = self.dialog()
        d._copy()
        self.assertEqual(QGuiApplication.clipboard().text(), entry.authorized_line)

    def test_delete_removes_everywhere_after_confirmation(self):
        from PySide6.QtWidgets import QMessageBox

        entry, _ = self.add_key()
        conn = Connection("web", "h", key_id=entry.id)
        ident = Identity("I", key_id=entry.id)
        self.cfg.connections.append(conn)
        self.cfg.identities.append(ident)
        d = self.dialog()
        asked = []

        def no(*a):
            asked.append(a[2])
            return QMessageBox.StandardButton.No

        with mock.patch.object(QMessageBox, "question", no):
            d._delete()
        self.assertEqual(len(self.cfg.ssh_keys), 1)
        self.assertIn("web", asked[0])
        self.assertIn("Identität I", asked[0])
        with mock.patch.object(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Yes):
            d._delete()
        self.assertEqual((self.cfg.ssh_keys, conn.key_id, ident.key_id), ([], "", ""))
        self.assertFalse(keystore.FileStore().exists(entry.id))
        self.assertEqual((self.saved, d.list.count()), (1, 0))


class InstallDialogTests(KeyGuiBase):
    def setUp(self):
        super().setUp()
        self.entry, _ = self.add_key()
        self.cfg.connections += [Connection("b-web", "h2", user="u"), Connection("a-db", "h1")]

    def make(self):
        from sesam.gui.keys_dialog import InstallKeyDialog

        d = InstallKeyDialog(self.cfg, self.entry)
        self.addCleanup(d.close)
        return d

    def test_lists_connections_sorted_and_installs(self):
        d = self.make()
        self.assertEqual([d.target.itemText(i).split()[0] for i in range(d.target.count())], ["a-db", "b-web"])
        d.target.setCurrentIndex(1)
        calls = []

        def fake(cfg, conn, line, password=None, **kw):
            calls.append((conn.name, line, password))
            return True

        with mock.patch.object(keys, "install_public_key", fake):
            d._install()
            self.pump(lambda: d.install_btn.isEnabled())
        self.assertEqual(calls, [("b-web", self.entry.authorized_line, None)])
        self.assertIn("eingetragen", d.status.text())

    def test_already_present_message(self):
        d = self.make()
        with mock.patch.object(keys, "install_public_key", lambda *a, **k: False):
            d._install()
            self.pump(lambda: d.install_btn.isEnabled())
        self.assertIn("bereits", d.status.text())

    def test_auth_error_asks_for_password_and_retries(self):
        from PySide6.QtWidgets import QInputDialog

        d = self.make()
        calls = []

        def fake(cfg, conn, line, password=None, **kw):
            calls.append(password)
            if password is None:
                raise sftp.SftpAuthError("Anmeldung abgelehnt")
            return True

        with mock.patch.object(keys, "install_public_key", fake), \
                mock.patch.object(QInputDialog, "getText", lambda *a, **k: ("geheim", True)):
            d._install()
            self.pump(lambda: len(calls) == 2 and d.install_btn.isEnabled())
        self.assertEqual(calls, [None, "geheim"])
        self.assertIn("eingetragen", d.status.text())

    def test_cancelled_password_and_other_errors(self):
        from PySide6.QtWidgets import QInputDialog

        d = self.make()
        with mock.patch.object(keys, "install_public_key", mock.Mock(side_effect=sftp.SftpAuthError("x"))), \
                mock.patch.object(QInputDialog, "getText", lambda *a, **k: ("", False)):
            d._install()
            self.pump(lambda: d.install_btn.isEnabled())
        self.assertIn("abgebrochen", d.status.text())
        with mock.patch.object(keys, "install_public_key", mock.Mock(side_effect=sftp.SftpError("Host nicht erreichbar"))):
            d._install()
            self.pump(lambda: d.install_btn.isEnabled() and "Host nicht erreichbar" in d.status.text())

    def test_without_connections_button_disabled(self):
        self.cfg.connections.clear()
        self.assertFalse(self.make().install_btn.isEnabled())


class AssignmentDialogTests(KeyGuiBase):
    def test_connection_dialog_assigns_key_and_disables_file_field(self):
        from sesam.gui.connection_dialog import ConnectionDialog

        entry, _ = self.add_key()
        conn = Connection("web", "h", identity_file="/alt")
        self.cfg.connections.append(conn)
        d = ConnectionDialog(self.cfg, conn, save_callback=self.save)
        self.addCleanup(d.close)
        self.assertTrue(d.identity.isEnabled())
        d.key_combo.setCurrentIndex(d.key_combo.findData(entry.id))
        self.assertFalse(d.identity.isEnabled() or d._browse_btn.isEnabled())
        d._accept()
        self.assertEqual((conn.key_id, conn.identity_file), (entry.id, ""))

    def test_connection_dialog_reopens_with_key_and_can_unassign(self):
        from sesam.gui.connection_dialog import ConnectionDialog

        entry, _ = self.add_key()
        conn = Connection("web", "h", key_id=entry.id)
        d = ConnectionDialog(self.cfg, conn)
        self.addCleanup(d.close)
        self.assertEqual(d.key_combo.currentData(), entry.id)
        self.assertFalse(d.identity.isEnabled())
        d.key_combo.setCurrentIndex(0)
        self.assertTrue(d.identity.isEnabled())
        d.identity.setText("/neu")
        d._accept()
        self.assertEqual((conn.key_id, conn.identity_file), ("", "/neu"))

    def test_identity_selection_overrides_key_combo(self):
        from sesam.gui.connection_dialog import ConnectionDialog

        self.add_key()
        ident = Identity("I", user="u")
        self.cfg.identities.append(ident)
        d = ConnectionDialog(self.cfg, Connection("web", "h", identity_id=ident.id))
        self.addCleanup(d.close)
        self.assertFalse(d.key_combo.isEnabled())
        self.assertFalse(d.identity.isEnabled())

    def test_identity_dialog_assigns_key(self):
        from sesam.gui.identities_dialog import IdentityEditDialog

        entry, _ = self.add_key()
        ident = Identity("I", user="u", identity_file="/alt")
        d = IdentityEditDialog(ident, cfg=self.cfg)
        self.addCleanup(d.close)
        d.key_combo.setCurrentIndex(d.key_combo.findData(entry.id))
        self.assertFalse(d.key.isEnabled())
        d._accept()
        self.assertEqual((ident.key_id, ident.identity_file), (entry.id, ""))

    def test_identity_dialog_without_cfg_still_works(self):
        from sesam.gui.identities_dialog import IdentityEditDialog

        d = IdentityEditDialog(Identity("I"))
        self.addCleanup(d.close)
        self.assertEqual(d.key_combo.count(), 1)


class MainWindowKeyTests(KeyGuiBase):
    def test_sidebar_action_and_icon(self):
        storage.save(Config(), self.path)
        w = self._window()
        self.addCleanup(w.close)
        self.assertEqual(w.a_keys.text(), "Schlüssel …")
        self.assertFalse(w.a_keys.icon().isNull())
        self.assertIn("a_keys", [a for g in w.SIDEBAR_GROUPS for a in g])
        self.assertFalse(w.menuBar().isVisible() and w.menuBar().actions())  # kein Menü mehr, alles in der Seitenleiste

    def test_opening_dialog_from_window(self):
        from sesam.gui import main_window

        storage.save(Config(), self.path)
        w = self._window()
        self.addCleanup(w.close)
        opened = []
        with mock.patch.object(main_window.KeysDialog, "exec", lambda self: opened.append(self) or 0):
            w.a_keys.trigger()
        self.assertEqual(len(opened), 1)
        self.assertIs(opened[0].cfg, w.cfg)


if __name__ == "__main__":
    unittest.main()
