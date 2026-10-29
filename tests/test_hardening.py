"""Härtung gegen manipulierte Eingaben: Hosts mit Optionen, Pfade aus Sync-Repository und SFTP-Server, beschädigte Konfiguration."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

try:
    import paramiko
except ImportError:  # paramiko nicht installiert
    paramiko = None

from sesam import cisco, connect_cli, launcher, storage, syncing
from sesam.models import Config, Connection, SyncSettings, Terminal

if paramiko is not None:
    from sesam import keys, sftp
    from sftp_server import SftpTestServer, _Files


class LauncherTargetTests(unittest.TestCase):
    def test_host_that_looks_like_an_option_is_rejected(self):
        for host in ("-oProxyCommand=touch /tmp/x", "-o", "a b", "h\nx", "h\x00"):
            with self.subTest(host=host), self.assertRaises(launcher.LaunchError):
                launcher.build_ssh_args(Connection("n", host))

    def test_user_and_jump_host_are_checked_too(self):
        with self.assertRaises(launcher.LaunchError):
            launcher.build_ssh_args(Connection("n", "h", user="-oProxyCommand=x"))
        with self.assertRaises(launcher.LaunchError):
            launcher.build_ssh_args(Connection("n", "h", jump_host="ok@j1,-oProxyCommand=x"))
        launcher.build_ssh_args(Connection("n", "h", user="u", jump_host="a@j1:2222, b@j2"))  # gültig

    def test_terminal_command_checks_the_target_as_well(self):
        term = Terminal("t", "putty.exe -ssh -P {port} {destination}")
        with self.assertRaises(launcher.LaunchError):
            launcher.build_terminal_command(term, Connection("n", "-oProxyCommand=x"))

    def test_destination_follows_double_dash(self):
        args = launcher.build_ssh_args(Connection("n", "h", user="u", remote_command="ls"))
        self.assertEqual(args[-4:], ["-t", "--", "u@h", "ls"])

    def test_unbalanced_quotes_raise_launch_error(self):
        with self.assertRaises(launcher.LaunchError):
            launcher.build_ssh_args(Connection("n", "h", extra_args='-o "offen'))
        with self.assertRaises(launcher.LaunchError):
            launcher.build_terminal_command(Terminal("t", 'xterm -e "{ssh_args}'), Connection("n", "h"))


class KnownHostsNameTests(unittest.TestCase):
    MSG = "Host key for {} has changed and you have requested strict checking.\n"

    def test_name_of_a_foreign_host_from_a_banner_is_ignored(self):
        conn = Connection("n", "ziel", port=2222)
        self.assertEqual(connect_cli.known_hosts_name(conn, self.MSG.format("github.com")), "[ziel]:2222")

    def test_own_hosts_are_accepted(self):
        conn = Connection("n", "ziel", jump_host="admin@bastion:2200,j2")
        self.assertEqual(connect_cli.known_hosts_name(conn, self.MSG.format("[bastion]:2200")), "[bastion]:2200")
        self.assertEqual(connect_cli.known_hosts_name(conn, self.MSG.format("ZIEL")), "ZIEL")


class StorageRobustnessTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "config.json"

    def load(self, text):
        self.path.write_text(text, encoding="utf-8")
        return storage.load(self.path)

    def test_infinite_numbers_do_not_break_the_start(self):
        cfg = self.load('{"font_scale": Infinity, "column_widths": [Infinity, NaN, 120, true]}')
        self.assertEqual((cfg.font_scale, cfg.column_widths), (100, [120]))

    def test_deeply_nested_json_is_a_config_error(self):
        with self.assertRaises(storage.ConfigError):
            self.load("[" * 100000)

    def test_entries_with_wrong_types_get_defaults(self):
        cfg = self.load('{"connections": [{"name": null, "host": 5, "port": "abc", "docker": "ja"}]}')
        c = cfg.connections[0]
        self.assertEqual((c.name, c.host, c.port, c.docker), ("", "", 22, False))
        self.assertEqual(cfg.connections[0].sudo_use_password, True)

    def test_port_outside_the_range_means_default(self):
        self.assertEqual(self.load('{"connections": [{"name": "a", "host": "h", "port": 70000}]}').connections[0].port, 22)


class CiscoInputTests(unittest.TestCase):
    def test_port_name_with_trailing_newline_is_rejected(self):
        with self.assertRaises(cisco.CiscoError):
            cisco.check_port_name("Gi0/1\n")

    def test_description_with_control_characters_is_rejected(self):
        for text in ("x\x1ay", "a\x1bb", "a\x03b"):
            with self.subTest(text=text), self.assertRaises(cisco.CiscoError):
                cisco.check_description(text)
        self.assertEqual(cisco.check_description("  Uplink   Raum 3 "), "Uplink Raum 3")


class _Store:
    def get(self, key):
        return None

    def set(self, key, value):
        pass

    def delete(self, key):
        pass


class SyncInputTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)

    def engine(self, passwords=False, phrase=None):
        eng = syncing.SyncEngine(SyncSettings(url="x", passwords=passwords), self.base / "work", phrase, _Store())
        eng.repo.mkdir(parents=True, exist_ok=True)
        return eng

    def put(self, eng, folder, name, text):
        (eng.repo / folder).mkdir(exist_ok=True)
        (eng.repo / folder / name).write_text(text, encoding="utf-8")

    def test_damaged_or_hostile_files_are_skipped_not_fatal(self):
        eng = self.engine()
        good = json.dumps({"id": "ok1", "name": "gut", "host": "h"})
        self.put(eng, "connections", "good.json", good)
        self.put(eng, "connections", "list.json", "[1]")
        self.put(eng, "connections", "noname.json", json.dumps({"id": "n1", "host": "h"}))
        self.put(eng, "connections", "number.json", json.dumps({"id": 5, "name": "n", "host": "h"}))
        self.put(eng, "connections", "traversal.json", json.dumps({"id": "../../outside/x", "name": "n", "host": "h"}))
        self.put(eng, "connections", "absolute.json", json.dumps({"id": "/etc/x", "name": "n", "host": "h"}))
        self.put(eng, "connections", "binary.json", "")
        (eng.repo / "connections" / "binary.json").write_bytes(b"\xff\xfe\x00")  # kein gültiges UTF-8
        remote = eng._read_remote(True)
        self.assertEqual(sorted(k[1] for k in remote.items), ["ok1"])  # ohne Namen, mit Zahl/Pfad als ID oder ohne gültiges UTF-8: übersprungen

    def test_groups_file_with_wrong_shape_is_ignored(self):
        eng = self.engine()
        for text in ("[1]", '{"groups": "x"}', '{"groups": [["a"], 3, "ok"]}'):
            (eng.repo / "groups.json").write_text(text, encoding="utf-8")
            groups = eng._read_remote(True).groups
            self.assertLessEqual(groups, {"ok"})

    def test_write_repo_refuses_unsafe_ids(self):
        eng = self.engine()
        evil = syncing.Item("c", "../../outside/pwned", {"id": "../../outside/pwned", "name": "n", "host": "h"})
        state = syncing.RemoteState({}, set(), None, None, False)
        with self.assertRaises(syncing.SyncError):
            eng._write_repo({evil.key: evil}, set(), state, None)
        self.assertFalse((self.base / "outside").exists())
        self.assertFalse(list(self.base.rglob("pwned*")))

    def test_symlinks_in_the_repository_are_rejected(self):
        eng = self.engine()
        victim = self.base / "victim"
        victim.mkdir()
        (victim / "wichtig.json").write_text("{}")
        try:
            os.symlink(victim, eng.repo / "connections")
        except (OSError, NotImplementedError):
            self.skipTest("Symlinks nicht möglich")
        with self.assertRaises(syncing.SyncError):
            eng._reject_symlinks()

    def test_meta_json_is_validated(self):
        eng = self.engine(True, "geheim123")
        cases = {
            "keine Objekt": "[1]",
            "n zu groß": json.dumps({"kdf": {"name": "scrypt", "n": 2**22, "r": 8, "p": 1, "salt": "AAAA"}, "check": {}}),
            "n kein int": json.dumps({"kdf": {"name": "scrypt", "n": "16", "r": 8, "p": 1, "salt": "AAAA"}, "check": {}}),
            "Salz fehlt": json.dumps({"kdf": {"name": "scrypt", "n": 16, "r": 8, "p": 1}, "check": {}}),
            "check fehlt": json.dumps({"kdf": {"name": "scrypt", "n": 16, "r": 8, "p": 1, "salt": "AAAA"}}),
            "check kaputt": json.dumps({"kdf": {"name": "scrypt", "n": 16, "r": 8, "p": 1, "salt": "AAAA"}, "check": "x"}),
        }
        for label, text in cases.items():
            with self.subTest(label), self.assertRaises(syncing.SyncError):
                (eng.repo / "meta.json").write_text(text, encoding="utf-8")
                eng._read_remote(True)

    def test_branch_and_url_never_look_like_options(self):
        for bad in ("--upload-pack=x", "-x", "a b", "a..b", "main.lock", ""):
            with self.subTest(branch=bad), self.assertRaises(syncing.SyncError):
                syncing.check_branch(bad)
        self.assertEqual(syncing.check_branch("feature/x-1.2"), "feature/x-1.2")
        for bad in ("--upload-pack=touch /tmp/x", "-oProxyCommand=x", "https://h/r x"):
            with self.subTest(url=bad), self.assertRaises(syncing.SyncError):
                syncing.check_url(bad)
        self.assertEqual(syncing.check_url(" https://h/r.git "), "https://h/r.git")

    def test_credentials_are_not_sent_over_plain_http(self):
        with self.assertRaises(syncing.SyncError):
            syncing.load_credentials(SyncSettings(url="http://h/r.git", username="u"))
        self.assertEqual(syncing.load_credentials(SyncSettings(url="https://h/r.git", username="u")), ("u", ""))
        with self.assertRaises(syncing.SyncError):
            syncing.check_remote("http://h/r.git", ("u", "pw"))


class SafeNameTests(unittest.TestCase):
    @unittest.skipIf(paramiko is None, "paramiko nicht verfügbar")
    def test_names_must_be_single_path_components(self):
        for bad in ("", ".", "..", "../x", "a/b", "/etc/x", "a\\b", "..\\x", "a\x00b"):
            with self.subTest(name=bad):
                self.assertFalse(sftp.safe_name(bad))
        for good in ("datei.txt", "..versteckt", "a b", "ä"):
            with self.subTest(name=good):
                self.assertTrue(sftp.safe_name(good))


@unittest.skipIf(paramiko is None, "paramiko nicht verfügbar")
class HostileServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.host_key = paramiko.RSAKey.generate(2048)

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.remote = self.base / "remote"
        (self.remote / "sub").mkdir(parents=True)
        (self.remote / "escaped.txt").write_text("vom Server geschrieben")
        self.dest = self.base / "downloads"
        self.dest.mkdir()
        self.server = SftpTestServer(str(self.remote), host_key=self.host_key)
        self.addCleanup(self.server.close)
        self.addCleanup(setattr, sftp, "host_key_confirmer", sftp.host_key_confirmer)
        sftp.host_key_confirmer = lambda *args: True  # Testserver: Host-Schlüssel gilt als bestätigt

    def serve_names(self, *names):
        """Der Server nennt in „/sub“ Einträge mit den angegebenen (bösartigen) Namen."""
        remote = self.remote

        class Hostile(_Files):
            def list_folder(self, path):
                if not self._real(path).rstrip("/").endswith("sub"):
                    return super().list_folder(path)
                out = []
                for name in names:
                    attr = paramiko.SFTPAttributes.from_stat(os.stat(remote / "escaped.txt"))
                    attr.filename = name
                    out.append(attr)
                return out

        self.server._files = type("Files", (Hostile,), {"ROOT": str(self.remote)})

    def session(self):
        conn = Connection(name="t", host="127.0.0.1", port=self.server.port, user="tester")
        session = sftp.SftpSession.connect(Config(), conn, password="geheim", known_hosts=self.base / "kh")
        self.addCleanup(session.close)
        return session

    def test_download_never_writes_outside_the_target_folder(self):
        for name in ("../../escaped.txt", "/tmp/sesam-hostile-test", "..\\..\\x"):
            with self.subTest(name=name):
                self.serve_names(name)
                with self.assertRaises(sftp.SftpError):
                    self.session().download(["/sub"], str(self.dest))
                self.assertFalse((self.base / "escaped.txt").exists())
                self.assertFalse(Path("/tmp/sesam-hostile-test").exists())
                self.assertEqual([p.name for p in self.base.iterdir() if p.name not in ("remote", "downloads", "kh")], [])

    def test_listing_hides_entries_with_paths_as_names(self):
        self.serve_names("../../escaped.txt", "gut.txt", "a/b")
        self.assertEqual([e.name for e in self.session().listdir("/sub")], ["gut.txt"])

    def test_passphrase_protected_key_is_reported_as_such(self):
        material = keys.generate("ed25519", "geheim")
        key_file = self.base / "id"
        key_file.write_text(material.private)
        conn = Connection(name="t", host="127.0.0.1", port=self.server.port, user="tester", identity_file=str(key_file))
        with self.assertRaises(sftp.SftpAuthError) as ctx:
            sftp.open_client(Config(), conn, known_hosts=self.base / "kh")
        self.assertIn("Passphrase", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()


class WindowsQuotingTests(unittest.TestCase):
    def test_powershell_quotes_everything_literally(self):
        from sesam.launcher import powershell_join
        out = powershell_join(["ssh", "-t", "u@h", "echo $(calc); `id` 'x' ‘y’ \"z\""])
        self.assertEqual(out, "& 'ssh' '-t' 'u@h' 'echo $(calc); `id` ''x'' ‘‘y’’ \"z\"'")

    def test_cmd_rejects_metacharacters(self):
        from sesam.launcher import LaunchError, cmd_join
        self.assertEqual(cmd_join(["ssh", "u@h", "ls -l"]), 'ssh u@h "ls -l"')
        for bad in ("a&calc", "a|b", "a>b", "%PATH%", "^x", 'a"b', "a\nb"):
            with self.assertRaises(LaunchError, msg=bad):
                cmd_join(["ssh", bad])

    def test_terminal_template_picks_the_quoting_by_program(self):
        from sesam.launcher import build_terminal_command
        from sesam.models import Connection, Terminal
        conn = Connection("n", "h")
        ps = build_terminal_command(Terminal("p", "powershell.exe -NoExit -Command {ssh_command}"), conn, ["sesam", "--remote-command", "x; calc"])
        self.assertEqual(ps[-1], "& 'sesam' '--remote-command' 'x; calc'")
        cmd = build_terminal_command(Terminal("c", "cmd.exe /k {ssh_command}"), conn, ["sesam", "--connect", "id"])
        self.assertEqual(cmd[-1], "sesam --connect id")


class HostKeyConfirmationTests(unittest.TestCase):
    def setUp(self):
        import paramiko
        from sesam import sftp
        self.sftp, self.paramiko = sftp, paramiko
        self.key = paramiko.RSAKey.generate(1024)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.hosts = Path(self.tmp.name) / "known_hosts"
        self.addCleanup(setattr, sftp, "host_key_confirmer", sftp.host_key_confirmer)

    def _missing(self):
        client = self.paramiko.SSHClient()
        self.sftp._make_host_key_policy(self.hosts).missing_host_key(client, "h.example", self.key)
        return client

    def test_unknown_host_is_rejected_without_confirmer(self):
        self.sftp.host_key_confirmer = None
        with self.assertRaises(self.paramiko.SSHException):
            self._missing()
        self.assertFalse(self.hosts.exists())

    def test_declined_confirmation_rejects_and_writes_nothing(self):
        self.sftp.host_key_confirmer = lambda *a: False
        with self.assertRaises(self.paramiko.SSHException):
            self._missing()
        self.assertFalse(self.hosts.exists())

    def test_accepted_confirmation_is_remembered_with_fingerprint(self):
        seen = []
        self.sftp.host_key_confirmer = lambda host, kind, fp: seen.append((host, kind, fp)) or True
        self._missing()
        self.assertEqual(seen[0][:2], ("h.example", "ssh-rsa"))
        self.assertTrue(seen[0][2].startswith("SHA256:") and "=" not in seen[0][2])
        self.assertIn("h.example ssh-rsa", self.hosts.read_text(encoding="utf-8"))
