"""Tests für das SFTP-Backend gegen einen echten, aber lokalen SSH/SFTP-Server (paramiko im selben Prozess)."""
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))

try:
    import paramiko
except ImportError:  # paramiko nicht installiert
    paramiko = None

from sesam import passwords
from sesam.models import Config, Connection

if paramiko is not None:
    from sesam import sftp
    from sftp_server import SftpTestServer


@unittest.skipIf(paramiko is None, "paramiko nicht verfügbar")
class SftpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.host_key = paramiko.RSAKey.generate(2048)

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        self.remote_root = base / "remote"
        self.local = base / "local"
        self.known_hosts = base / "known_hosts"
        self.remote_root.mkdir()
        self.local.mkdir()
        self.server = SftpTestServer(str(self.remote_root), host_key=self.host_key)
        self.addCleanup(self.server.close)
        self.cfg = Config()
        self.conn = Connection(name="Test", host="127.0.0.1", port=self.server.port, user="tester")

    def connect(self, password="geheim", conn=None):
        session = sftp.SftpSession.connect(self.cfg, conn or self.conn, password=password, known_hosts=self.known_hosts)
        self.addCleanup(session.close)
        return session

    # --- Verbindung ---
    def test_connect_remembers_new_host_key(self):
        self.connect()
        text = self.known_hosts.read_text()
        self.assertTrue(text.startswith(f"[127.0.0.1]:{self.server.port} ssh-rsa "), text)

    def test_known_hosts_gets_one_line_appended_and_keeps_existing_content(self):
        self.known_hosts.write_text("# mein Kommentar\nanderer.host ssh-ed25519 AAAA")  # ohne Zeilenende am Schluss
        self.connect()
        lines = self.known_hosts.read_text().splitlines()
        self.assertEqual(lines[0], "# mein Kommentar")
        self.assertEqual(lines[1], "anderer.host ssh-ed25519 AAAA")
        self.assertEqual(len(lines), 3)

    def test_changed_host_key_is_rejected(self):
        other = paramiko.RSAKey.generate(2048)
        self.known_hosts.write_text(f"[127.0.0.1]:{self.server.port} {other.get_name()} {other.get_base64()}\n")
        with self.assertRaises(sftp.SftpError) as ctx:
            self.connect()
        self.assertIn("geändert", str(ctx.exception))

    def test_wrong_password_raises_auth_error(self):
        with self.assertRaises(sftp.SftpAuthError):
            self.connect(password="falsch")

    def test_no_password_and_no_key_raises_auth_error(self):
        # Ohne Passwort und Schlüssel meldet paramiko „keine Methode“; das muss als Anmeldeproblem erkennbar sein (Nachfrage im Fenster).
        with self.assertRaises(sftp.SftpAuthError):
            sftp.SftpSession.connect(self.cfg, self.conn, known_hosts=self.known_hosts)

    def test_close_is_safe_twice_and_after_connection_loss(self):
        s = self.connect()
        s.close()
        s.close()
        s2 = self.connect()
        self.server.close()  # beendet auch die laufenden Transporte
        s2.close()  # darf nicht werfen

    def test_jump_host_is_not_supported(self):
        conn = Connection(name="J", host="127.0.0.1", port=self.server.port, user="tester", jump_host="a@b")
        with self.assertRaises(sftp.SftpError) as ctx:
            self.connect(conn=conn)
        self.assertIn("Jump-Host", str(ctx.exception))

    def test_unreachable_host_raises_readable_error(self):
        import socket

        with socket.socket() as probe:  # freien Port ermitteln und wieder freigeben: dort lauscht niemand
            probe.bind(("127.0.0.1", 0))
            free_port = probe.getsockname()[1]
        conn = Connection(name="Test", host="127.0.0.1", port=free_port, user="tester")
        with self.assertRaises(sftp.SftpError) as ctx:
            self.connect(conn=conn)
        self.assertIn("fehlgeschlagen", str(ctx.exception))

    def test_saved_password_comes_from_keyring(self):
        conn = Connection(name="Test", host="127.0.0.1", port=self.server.port, user="tester", use_password=True)
        with mock.patch.object(passwords, "get_password", return_value="geheim") as get:
            session = sftp.SftpSession.connect(self.cfg, conn, known_hosts=self.known_hosts)
            self.addCleanup(session.close)
        get.assert_called_once_with(conn.id)

    def test_identity_supplies_user_and_password_key(self):
        from sesam.models import Identity

        ident = Identity(name="I", user="tester", use_password=True)
        self.cfg.identities.append(ident)
        conn = Connection(name="Test", host="127.0.0.1", port=self.server.port, identity_id=ident.id)
        with mock.patch.object(passwords, "get_password", return_value="geheim") as get:
            session = sftp.SftpSession.connect(self.cfg, conn, known_hosts=self.known_hosts)
            self.addCleanup(session.close)
        get.assert_called_once_with(ident.id)

    # --- Ordner ---
    def test_home_and_listdir(self):
        (self.remote_root / "a.txt").write_text("hallo")
        (self.remote_root / "ordner").mkdir()
        s = self.connect()
        self.assertEqual(s.home(), "/")
        entries = {e.name: e for e in s.listdir("/")}
        self.assertEqual(set(entries), {"a.txt", "ordner"})
        self.assertFalse(entries["a.txt"].is_dir)
        self.assertEqual(entries["a.txt"].size, 5)
        self.assertTrue(entries["ordner"].is_dir)

    @unittest.skipUnless(hasattr(os, "symlink") and sys.platform != "win32", "Symlinks")
    def test_symlink_to_directory_is_listed_as_directory(self):
        (self.remote_root / "ziel").mkdir()
        os.symlink(self.remote_root / "ziel", self.remote_root / "link")
        s = self.connect()
        entries = {e.name: e for e in s.listdir("/")}
        self.assertTrue(entries["link"].is_dir)
        self.assertTrue(entries["link"].is_link)

    def test_listdir_missing_folder_raises(self):
        with self.assertRaises(sftp.SftpError):
            self.connect().listdir("/gibt-es-nicht")

    def test_mkdir_rename_and_recursive_remove(self):
        s = self.connect()
        s.mkdir("/neu")
        self.assertTrue((self.remote_root / "neu").is_dir())
        s.rename("/neu", "/neuer")
        self.assertTrue((self.remote_root / "neuer").is_dir())
        (self.remote_root / "neuer" / "tief" / "tiefer").mkdir(parents=True)
        (self.remote_root / "neuer" / "tief" / "tiefer" / "x.bin").write_bytes(b"1")
        (self.remote_root / "neuer" / "y.txt").write_text("y")
        s.remove("/neuer")
        self.assertFalse((self.remote_root / "neuer").exists())

    @unittest.skipUnless(hasattr(os, "symlink") and sys.platform != "win32", "Symlinks")
    def test_remove_symlink_does_not_touch_target(self):
        (self.remote_root / "ziel").mkdir()
        (self.remote_root / "ziel" / "wichtig.txt").write_text("bleibt")
        os.symlink(self.remote_root / "ziel", self.remote_root / "link")
        self.connect().remove("/link")
        self.assertFalse((self.remote_root / "link").exists())
        self.assertEqual((self.remote_root / "ziel" / "wichtig.txt").read_text(), "bleibt")

    # --- Übertragungen ---
    def make_local_tree(self):
        (self.local / "datei.txt").write_text("eins")
        tree = self.local / "projekt"
        (tree / "sub" / "leer").mkdir(parents=True)
        (tree / "a.txt").write_text("aaa")
        (tree / "sub" / "b.bin").write_bytes(os.urandom(5000))
        return tree

    def test_upload_files_and_folders_with_progress(self):
        tree = self.make_local_tree()
        s = self.connect()
        calls = []
        count = s.upload([str(self.local / "datei.txt"), str(tree)], "/", progress=lambda d, t, n: calls.append((d, t, n)))
        self.assertEqual(count, 3)
        self.assertEqual((self.remote_root / "datei.txt").read_text(), "eins")
        self.assertEqual((self.remote_root / "projekt" / "a.txt").read_text(), "aaa")
        self.assertEqual((self.remote_root / "projekt" / "sub" / "b.bin").read_bytes(), (tree / "sub" / "b.bin").read_bytes())
        self.assertTrue((self.remote_root / "projekt" / "sub" / "leer").is_dir())  # leere Ordner bleiben erhalten
        total = 4 + 3 + 5000
        self.assertEqual(calls[-1][:2], (total, total))
        self.assertTrue(all(d <= t == total for d, t, _ in calls))

    def test_upload_skip_leaves_existing_remote_file_untouched(self):
        (self.remote_root / "datei.txt").write_text("alt")
        (self.local / "datei.txt").write_text("neu")
        (self.local / "zweite.txt").write_text("zwei")
        count = self.connect().upload([str(self.local / "datei.txt"), str(self.local / "zweite.txt")], "/", skip={"datei.txt"})
        self.assertEqual(count, 1)
        self.assertEqual((self.remote_root / "datei.txt").read_text(), "alt")
        self.assertEqual((self.remote_root / "zweite.txt").read_text(), "zwei")

    def test_upload_overwrites_when_not_skipped(self):
        (self.remote_root / "datei.txt").write_text("alt-und-länger")
        (self.local / "datei.txt").write_text("neu")
        self.connect().upload([str(self.local / "datei.txt")], "/")
        self.assertEqual((self.remote_root / "datei.txt").read_text(), "neu")

    def test_download_files_and_folders(self):
        (self.remote_root / "datei.txt").write_text("eins")
        (self.remote_root / "projekt" / "sub" / "leer").mkdir(parents=True)
        (self.remote_root / "projekt" / "sub" / "b.bin").write_bytes(b"\x00\x01" * 3000)
        dest = self.local / "ziel"
        dest.mkdir()
        calls = []
        count = self.connect().download(["/datei.txt", "/projekt"], str(dest), progress=lambda d, t, n: calls.append((d, t, n)))
        self.assertEqual(count, 2)
        self.assertEqual((dest / "datei.txt").read_text(), "eins")
        self.assertEqual((dest / "projekt" / "sub" / "b.bin").read_bytes(), b"\x00\x01" * 3000)
        self.assertTrue((dest / "projekt" / "sub" / "leer").is_dir())
        self.assertEqual(calls[-1][:2], (6004, 6004))

    @unittest.skipUnless(hasattr(os, "symlink") and sys.platform != "win32", "Symlinks")
    def test_download_does_not_follow_directory_links(self):
        (self.remote_root / "ordner").mkdir()
        (self.remote_root / "ordner" / "x.txt").write_text("x")
        os.symlink(self.remote_root, self.remote_root / "ordner" / "schleife")  # würde endlos rekursieren
        dest = self.local / "ziel"
        dest.mkdir()
        self.connect().download(["/ordner"], str(dest))
        self.assertEqual((dest / "ordner" / "x.txt").read_text(), "x")
        self.assertFalse((dest / "ordner" / "schleife").exists())

    def test_cancel_upload_stops_and_removes_partial_file(self):
        big = self.local / "gross.bin"
        big.write_bytes(os.urandom(3 * 1024 * 1024))
        cancel = threading.Event()
        s = self.connect()

        def progress(done, total, name):
            if done > 0:
                cancel.set()

        with self.assertRaises(sftp.TransferCancelled):
            s.upload([str(big)], "/", progress=progress, cancel=cancel)
        self.assertFalse((self.remote_root / "gross.bin").exists())

    def test_cancel_download_removes_partial_file(self):
        (self.remote_root / "gross.bin").write_bytes(os.urandom(3 * 1024 * 1024))
        cancel = threading.Event()
        dest = self.local / "ziel"
        dest.mkdir()

        def progress(done, total, name):
            if done > 0:
                cancel.set()

        with self.assertRaises(sftp.TransferCancelled):
            self.connect().download(["/gross.bin"], str(dest), progress=progress, cancel=cancel)
        self.assertFalse((dest / "gross.bin").exists())

    def test_download_missing_file_raises_readable_error(self):
        with self.assertRaises(sftp.SftpError):
            self.connect().download(["/gibt-es-nicht.txt"], str(self.local))


if __name__ == "__main__":
    unittest.main()
