"""SSH-Schlüssel: erzeugen, einlesen, Dateispeicher, Zuweisung, Installation auf dem Server."""
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))

from sesam import keys, keystore, launcher, passwords
from sesam.models import Config, Connection, Identity, SshKey

try:
    import paramiko
except ImportError:
    paramiko = None


class TempKeyDir(unittest.TestCase):
    """Leitet den Schlüsselordner in ein Wegwerfverzeichnis um."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        patcher = mock.patch.object(keystore, "directory", lambda: self.tmp / "keys")
        patcher.start()
        self.addCleanup(patcher.stop)


class GenerateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ed = keys.generate("ed25519")

    def test_ed25519(self):
        self.assertEqual(self.ed.type, "ed25519")
        self.assertTrue(self.ed.public.startswith("ssh-ed25519 "))
        self.assertEqual(len(self.ed.public.split()), 2)  # ohne Kommentar
        self.assertTrue(self.ed.private.startswith("-----BEGIN OPENSSH PRIVATE KEY-----"))
        self.assertFalse(self.ed.encrypted)
        self.assertTrue(self.ed.fingerprint.startswith("SHA256:") and "=" not in self.ed.fingerprint)

    def test_rsa_uses_configured_size(self):
        with mock.patch.object(keys, "RSA_BITS", 2048):
            material = keys.generate("rsa")
        self.assertEqual(material.type, "rsa")
        self.assertTrue(material.public.startswith("ssh-rsa "))

    def test_unknown_type(self):
        with self.assertRaises(keys.KeyManagementError):
            keys.generate("dsa")

    def test_keys_are_different(self):
        self.assertNotEqual(keys.generate("ed25519").public, self.ed.public)

    @unittest.skipIf(shutil.which("ssh-keygen") is None, "ssh-keygen nicht verfügbar")
    def test_openssh_accepts_key_and_agrees_on_public_key_and_fingerprint(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "id"
            path.write_text(self.ed.private)
            path.chmod(0o600)
            public = subprocess.run(["ssh-keygen", "-y", "-f", str(path)], capture_output=True, text=True, check=True).stdout.strip()
            self.assertEqual(public.split()[:2], self.ed.public.split())
            fp = subprocess.run(["ssh-keygen", "-l", "-f", str(path)], capture_output=True, text=True, check=True).stdout.split()[1]
            self.assertEqual(fp, self.ed.fingerprint)

    @unittest.skipIf(paramiko is None, "paramiko nicht verfügbar")
    def test_paramiko_can_load_key_and_fingerprint_matches(self):
        import io

        pk = paramiko.Ed25519Key.from_private_key(io.StringIO(self.ed.private))
        self.assertEqual(pk.fingerprint, self.ed.fingerprint)

    @unittest.skipIf(shutil.which("ssh-keygen") is None, "ssh-keygen nicht verfügbar")
    def test_passphrase_protected_key_is_valid_for_openssh(self):
        material = keys.generate("ed25519", "geheim")
        self.assertTrue(material.encrypted)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "id"
            path.write_text(material.private)
            path.chmod(0o600)
            out = subprocess.run(["ssh-keygen", "-y", "-P", "geheim", "-f", str(path)], capture_output=True, text=True)
            self.assertEqual(out.stdout.split()[:2], material.public.split())
            bad = subprocess.run(["ssh-keygen", "-y", "-P", "falsch", "-f", str(path)], capture_output=True, text=True)
            self.assertNotEqual(bad.returncode, 0)


class ReadKeyTests(unittest.TestCase):
    def test_roundtrip_unencrypted(self):
        m = keys.generate("ed25519")
        got = keys.read_key(m.private)
        self.assertEqual((got.public, got.fingerprint, got.type, got.encrypted), (m.public, m.fingerprint, "ed25519", False))

    def test_encrypted_needs_passphrase_or_public_hint(self):
        m = keys.generate("ed25519", "pw")
        self.assertTrue(keys.read_key(m.private, "pw").encrypted)
        self.assertEqual(keys.read_key(m.private, "pw").public, m.public)
        with self.assertRaises(keys.NeedsPassphrase):
            keys.read_key(m.private)
        hinted = keys.read_key(m.private, public_hint=m.public + " kommentar")
        self.assertEqual((hinted.public, hinted.encrypted), (m.public, True))  # Kommentar entfällt

    def test_wrong_passphrase(self):
        m = keys.generate("ed25519", "pw")
        with self.assertRaises(keys.KeyManagementError) as ctx:
            keys.read_key(m.private, "falsch")
        self.assertIn("Passphrase", str(ctx.exception))

    def test_traditional_pem_rsa(self):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        key = rsa.generate_private_key(65537, 2048)
        pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                serialization.NoEncryption()).decode()
        got = keys.read_key(pem)
        self.assertEqual(got.type, "rsa")
        self.assertEqual(got.public.split()[0], "ssh-rsa")

    def test_garbage_and_putty(self):
        for text in ("kein schlüssel", "PuTTY-User-Key-File-3: ssh-ed25519\nEncryption: none\n", ""):
            with self.assertRaises(keys.KeyManagementError):
                keys.read_key(text)
        with self.assertRaises(keys.KeyManagementError):
            keys.read_key("-----BEGIN OPENSSH PRIVATE KEY-----\nAAAA\n-----END OPENSSH PRIVATE KEY-----\n")

    def test_read_key_file_uses_pub_file_for_encrypted_keys(self):
        m = keys.generate("ed25519", "pw")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "id_ed25519"
            path.write_text(m.private)
            with self.assertRaises(keys.NeedsPassphrase):
                keys.read_key_file(path)
            (Path(tmp) / "id_ed25519.pub").write_text(m.public + " me@host\n")
            got = keys.read_key_file(path)
            self.assertEqual((got.public, got.encrypted), (m.public, True))
            (Path(tmp) / "id_ed25519.pub").write_text("müll\n")  # unbrauchbare .pub-Datei: wie fehlend
            with self.assertRaises(keys.NeedsPassphrase):
                keys.read_key_file(path)

    def test_missing_file(self):
        with self.assertRaises(keys.KeyManagementError):
            keys.read_key_file("/gibt/es/nicht")

    def test_fingerprint_and_type_helpers(self):
        with self.assertRaises(keys.KeyManagementError):
            keys.fingerprint("ssh-ed25519")
        self.assertEqual(keys.type_of("ecdsa-sha2-nistp256 AAAA"), "ecdsa")
        self.assertEqual(keys.type_of("sk-ssh-ed25519@openssh.com AAAA"), "sk")
        self.assertEqual(keys.type_of(""), "?")


class FileStoreTests(TempKeyDir):
    def test_set_get_delete_exists(self):
        store = keystore.FileStore()
        self.assertIsNone(store.get("abc"))
        self.assertFalse(store.exists("abc"))
        store.set("abc", "geheimer-text")
        self.assertEqual(store.get("abc"), "geheimer-text\n")  # Zeilenumbruch am Ende ergänzt
        self.assertTrue(store.exists("abc"))
        store.set("abc", "neu\n")
        self.assertEqual(store.get("abc"), "neu\n")
        store.delete("abc")
        store.delete("abc")  # zweites Löschen ist kein Fehler
        self.assertIsNone(store.get("abc"))

    @unittest.skipIf(sys.platform.startswith("win"), "POSIX-Rechte")
    def test_permissions_are_private(self):
        keystore.FileStore().set("abc", "x")
        self.assertEqual(stat.S_IMODE(keystore.path_of("abc").stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(keystore.directory().stat().st_mode), 0o700)

    def test_no_temp_file_left_and_other_files_untouched(self):
        keystore.FileStore().set("abc", "x")
        self.assertEqual([p.name for p in keystore.directory().iterdir()], ["abc.key"])

    def test_unsafe_ids_are_rejected(self):
        store = keystore.FileStore()
        for bad in ("../x", "a/b", "", "a" * 65, "a b", "..", "x\x00y"):
            with self.assertRaises(passwords.PasswordError):
                store.set(bad, "x")
            with self.assertRaises(passwords.PasswordError):
                store.get(bad)
        self.assertFalse(store.exists("../x"))
        self.assertFalse((self.tmp / "x.key").exists())

    def test_write_error_becomes_password_error(self):
        (self.tmp / "keys").write_text("ich bin eine Datei, kein Ordner")
        with self.assertRaises(passwords.PasswordError):
            keystore.FileStore().set("abc", "x")


class AssignmentTests(TempKeyDir):
    def setUp(self):
        super().setUp()
        self.key = SshKey("Arbeit", id="k1")
        self.cfg = Config(ssh_keys=[self.key])

    def test_connection_with_key_uses_stored_file_as_identity(self):
        keystore.FileStore().set("k1", "PRIVAT")
        conn = Connection("a", "h", identity_file="/alt/pfad", key_id="k1")
        eff, secret = launcher.resolve(self.cfg, conn)
        self.assertEqual(eff.identity_file, str(keystore.path_of("k1")))
        self.assertEqual(secret, conn.id)
        self.assertIn(str(keystore.path_of("k1")), launcher.build_ssh_args(eff))

    def test_identity_key_wins_over_connection_key(self):
        other = SshKey("Privat", id="k2")
        self.cfg.ssh_keys.append(other)
        ident = Identity("I", user="u", key_id="k2")
        self.cfg.identities.append(ident)
        conn = Connection("a", "h", key_id="k1", identity_id=ident.id)
        eff, secret = launcher.resolve(self.cfg, conn)
        self.assertEqual((eff.key_id, eff.identity_file, secret), ("k2", str(keystore.path_of("k2")), ident.id))

    def test_identity_without_key_clears_connection_key(self):
        ident = Identity("I", user="u", identity_file="/id/datei")
        self.cfg.identities.append(ident)
        eff, _ = launcher.resolve(self.cfg, Connection("a", "h", key_id="k1", identity_id=ident.id))
        self.assertEqual((eff.key_id, eff.identity_file), ("", "/id/datei"))

    def test_without_key_nothing_changes(self):
        conn = Connection("a", "h", identity_file="/x")
        eff, _ = launcher.resolve(self.cfg, conn)
        self.assertEqual(eff.identity_file, "/x")
        launcher.check_key(self.cfg, eff)

    def test_unsafe_key_id_does_not_crash_resolve(self):
        eff, _ = launcher.resolve(self.cfg, Connection("a", "h", identity_file="/x", key_id="../böse"))
        self.assertEqual(eff.identity_file, "/x")

    def test_check_key_reports_missing_private_part_and_unknown_key(self):
        eff, _ = launcher.resolve(self.cfg, Connection("a", "h", key_id="k1"))
        with self.assertRaises(launcher.LaunchError) as ctx:
            launcher.check_key(self.cfg, eff)
        self.assertIn("Arbeit", str(ctx.exception))
        self.assertIn("nicht vorhanden", str(ctx.exception))
        keystore.FileStore().set("k1", "x")
        launcher.check_key(self.cfg, eff)
        eff, _ = launcher.resolve(self.cfg, Connection("a", "h", key_id="weg"))
        with self.assertRaises(launcher.LaunchError) as ctx:
            launcher.check_key(self.cfg, eff)
        self.assertIn("existiert nicht mehr", str(ctx.exception))

    def test_launch_refuses_missing_key_before_starting_terminal(self):
        from sesam.models import Terminal

        self.cfg.terminals = [Terminal("T", "xterm -e {ssh_args}")]
        with mock.patch.object(launcher.subprocess, "Popen") as popen, self.assertRaises(launcher.LaunchError):
            launcher.launch(self.cfg, Connection("a", "h", key_id="k1"))
        popen.assert_not_called()

    def test_users_of_and_forget(self):
        ident = Identity("I", key_id="k1")
        conn = Connection("web", "h", key_id="k1")
        keep = Connection("db", "h")
        self.cfg.identities.append(ident)
        self.cfg.connections += [conn, keep]
        self.assertEqual(keys.users_of(self.cfg, "k1"), ["web", "Identität I"])
        keys.forget(self.cfg, "k1")
        self.assertEqual((self.cfg.ssh_keys, conn.key_id, ident.key_id), ([], "", ""))

    def test_entry_from_material(self):
        m = keys.generate("ed25519")
        entry = keys.new_entry("  Mein Schlüssel ", m, " me@host ")
        self.assertEqual((entry.name, entry.type, entry.comment, entry.use_password), ("Mein Schlüssel", "ed25519", "me@host", True))
        self.assertEqual(entry.authorized_line, f"{m.public} me@host")
        self.assertEqual(len(entry.created), 10)
        self.assertEqual(SshKey("x", public_key="ssh-ed25519 AAAA").authorized_line, "ssh-ed25519 AAAA")


@unittest.skipIf(paramiko is None or shutil.which("sh") is None, "paramiko oder sh nicht verfügbar")
class InstallTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.host_key = paramiko.RSAKey.generate(2048)
        cls.key = keys.generate("ed25519")

    def setUp(self):
        from sftp_server import SftpTestServer

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.home = self.base / "home"
        self.home.mkdir()
        self.commands = []

        def run(cmd):  # führt den Befehl wirklich in einer Shell aus, mit eigenem Home-Ordner
            self.commands.append(cmd)
            p = subprocess.run(["sh", "-c", cmd], capture_output=True, env={"HOME": str(self.home), "PATH": os.environ["PATH"]})
            return p.stdout, p.stderr, p.returncode

        (self.base / "remote").mkdir()
        self.server = SftpTestServer(str(self.base / "remote"), host_key=self.host_key, exec_handler=run)
        self.addCleanup(self.server.close)
        self.conn = Connection("t", "127.0.0.1", port=self.server.port, user="tester")
        self.line = f"{self.key.public} test@sesam"

    def install(self, line=None, password="geheim"):
        return keys.install_public_key(Config(), self.conn, self.line if line is None else line, password=password,
                                       known_hosts=self.base / "known_hosts")

    @property
    def authorized(self):
        return self.home / ".ssh" / "authorized_keys"

    def test_installs_into_new_file_with_private_permissions(self):
        self.assertTrue(self.install())
        self.assertEqual(self.authorized.read_text(), self.line + "\n")
        self.assertEqual(stat.S_IMODE(self.authorized.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE((self.home / ".ssh").stat().st_mode), 0o700)

    def test_is_idempotent(self):
        self.assertTrue(self.install())
        self.assertFalse(self.install())
        self.assertEqual(self.authorized.read_text().count(self.line), 1)

    def test_keeps_existing_keys_even_without_trailing_newline(self):
        (self.home / ".ssh").mkdir()
        self.authorized.write_text("ssh-rsa AAAAalt alter-schluessel")  # kein Zeilenende
        self.assertTrue(self.install())
        self.assertEqual(self.authorized.read_text().splitlines(), ["ssh-rsa AAAAalt alter-schluessel", self.line])

    def test_similar_line_is_not_taken_for_present(self):
        (self.home / ".ssh").mkdir()
        self.authorized.write_text(self.line + "-anders\n")
        self.assertTrue(self.install())
        self.assertEqual(len(self.authorized.read_text().splitlines()), 2)

    def test_hostile_comment_cannot_inject_commands(self):
        evil = f"{self.key.public} x'; touch {self.base}/gehackt; echo '"
        self.install(evil)
        self.assertFalse((self.base / "gehackt").exists())
        self.assertEqual(self.authorized.read_text(), " ".join(evil.split()) + "\n")

    def test_invalid_lines_are_refused_without_connecting(self):
        for bad in ("", "nur-ein-wort", f"{self.key.public}\nssh-rsa AAAA", "ssh-ed25519 !!!"):
            with self.assertRaises(keys.KeyManagementError):
                self.install(bad)
        self.assertEqual(self.commands, [])

    def test_wrong_password_raises_auth_error(self):
        from sesam import sftp

        with self.assertRaises(sftp.SftpAuthError):
            self.install(password="falsch")

    def test_failing_command_reports_reason(self):
        self.server.close()
        from sftp_server import SftpTestServer

        server = SftpTestServer(str(self.base / "remote"), host_key=self.host_key,
                                exec_handler=lambda cmd: (b"", b"mkdir: Permission denied\n", 1))
        self.addCleanup(server.close)
        self.conn = replace(self.conn, port=server.port)
        with self.assertRaises(keys.KeyManagementError) as ctx:
            self.install()
        self.assertIn("Permission denied", str(ctx.exception))


class KeySyncTests(unittest.TestCase):
    """Schlüssel laufen über dieselbe Synchronisation wie Verbindungen."""

    def setUp(self):
        import test_sync

        self.helper = test_sync.EngineTests("test_first_device_pushes_encrypted_and_second_device_receives")
        self.helper.setUp()
        self.addCleanup(self.helper.tmp.cleanup)

    def test_key_travels_encrypted_and_private_part_follows_password_sync(self):
        cfgA, A, sA = self.helper.device("A")
        material = keys.generate("ed25519")
        entry = keys.new_entry("Arbeit", material, "me@a")
        cfgA.ssh_keys.append(entry)
        sA.set(entry.id, material.private)
        self.helper.add_conn(cfgA, sA, "web", key_id=entry.id)
        A.run(cfgA)
        files = self.helper.remote_text()
        self.assertIn(f"ssh_keys/{entry.id}.json", files)
        blob = "\n".join(files.values())
        self.assertNotIn("BEGIN OPENSSH", blob)
        self.assertNotIn(material.private.splitlines()[1], blob)
        self.assertIn(material.public, blob)

        cfgB, B, sB = self.helper.device("B")
        B.run(cfgB)
        got = cfgB.ssh_key_by_id(entry.id)
        self.assertEqual((got.name, got.fingerprint, got.use_password), ("Arbeit", material.fingerprint, True))
        self.assertEqual(sB.get(entry.id), material.private)
        self.assertEqual(cfgB.connections[0].key_id, entry.id)

    def test_without_password_sync_only_metadata_arrives(self):
        cfgA, A, sA = self.helper.device("A", passwords_on=False)
        entry = keys.new_entry("Nur Meta", keys.generate("ed25519"), "")
        cfgA.ssh_keys.append(entry)
        sA.set(entry.id, "PRIVAT")
        A.run(cfgA)
        self.assertNotIn("PRIVAT", "".join(self.helper.remote_text().values()))
        cfgB, B, sB = self.helper.device("B", passwords_on=False)
        B.run(cfgB)
        got = cfgB.ssh_key_by_id(entry.id)
        self.assertIsNotNone(got)
        self.assertFalse(got.use_password)
        self.assertIsNone(sB.get(entry.id))

    def test_deleting_key_propagates_and_clears_assignments(self):
        cfgA, A, sA = self.helper.device("A")
        entry = keys.new_entry("Weg", keys.generate("ed25519"), "")
        cfgA.ssh_keys.append(entry)
        sA.set(entry.id, "P")
        ident = Identity("I", key_id=entry.id)
        cfgA.identities.append(ident)
        self.helper.add_conn(cfgA, sA, "web", key_id=entry.id)
        A.run(cfgA)
        cfgB, B, sB = self.helper.device("B")
        B.run(cfgB)
        self.assertEqual(sB.get(entry.id), "P")

        keys.forget(cfgA, entry.id)
        sA.delete(entry.id)
        A.run(cfgA)
        B.run(cfgB)
        self.assertEqual(cfgB.ssh_keys, [])
        self.assertIsNone(sB.get(entry.id))
        self.assertEqual(cfgB.connections[0].key_id, "")
        self.assertEqual(cfgB.identities[0].key_id, "")

    def test_malicious_key_id_from_repo_is_not_written_to_disk(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(keystore, "directory", lambda: Path(tmp) / "keys"):
            engine_store = keystore.FileStore()
            with self.assertRaises(passwords.PasswordError):
                engine_store.set("../../evil", "x")
            self.assertFalse((Path(tmp) / "evil.key").exists())


if __name__ == "__main__":
    unittest.main()
