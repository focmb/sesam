"""Wrapper im Terminal: Verbindungsfehler bleiben sichtbar, geänderter Host-Schlüssel wird nach Rückfrage entfernt."""
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

from sesam import connect_cli, launcher
from sesam.models import Config, Connection, Terminal

CHANGED = (
    "@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@\n"
    "@    WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!     @\n"
    "@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@\n"
    "Offending ED25519 key in /home/u/.ssh/known_hosts:7\n"
    "Host key verification failed.\n"
)


class HelperTests(unittest.TestCase):
    def test_host_key_changed_is_recognised(self):
        self.assertTrue(connect_cli.host_key_changed(CHANGED))
        self.assertFalse(connect_cli.host_key_changed("ssh: connect to host h port 22: Connection refused\n"))

    def test_known_hosts_name_follows_the_port(self):
        self.assertEqual(connect_cli.known_hosts_name(Connection("n", "10.0.0.5")), "10.0.0.5")
        self.assertEqual(connect_cli.known_hosts_name(Connection("n", "10.0.0.5", port=2222)), "[10.0.0.5]:2222")

    def test_known_hosts_name_prefers_the_host_ssh_names(self):
        msg = "Host key for bastion.example has changed and you have requested strict checking.\n"
        self.assertEqual(connect_cli.known_hosts_name(Connection("n", "ziel", jump_host="admin@bastion.example:2200"), msg), "bastion.example")

    def test_forget_command(self):
        self.assertEqual(connect_cli.forget_command("[h]:2222"), ["ssh-keygen", "-R", "[h]:2222"])

    def test_run_ssh_returns_exit_code_and_captures_stderr(self):
        prog = "import sys; sys.stderr.write('kaputt\\n'); sys.exit(255)"
        err = io.StringIO()
        with redirect_stderr(err):
            code, captured = connect_cli.run_ssh([sys.executable, "-c", prog], {})
        self.assertEqual(code, 255)
        self.assertIn("kaputt", captured)
        self.assertIn("kaputt", err.getvalue())  # und bleibt sichtbar

    def test_run_ssh_without_error_output(self):
        code, captured = connect_cli.run_ssh([sys.executable, "-c", "pass"], {})
        self.assertEqual((code, captured), (0, ""))


class RunTests(unittest.TestCase):
    def setUp(self):
        self.conn = Connection("Server", "10.0.0.5", user="u")
        self.cfg = Config(connections=[self.conn])
        self.ssh = mock.patch.object(connect_cli, "run_ssh")
        self.run_ssh = self.ssh.start()
        self.addCleanup(self.ssh.stop)
        for target in (mock.patch.object(connect_cli.storage, "load", return_value=self.cfg),
                       mock.patch.object(connect_cli.shutil, "which", return_value="/usr/bin/ssh-keygen")):
            target.start()
            self.addCleanup(target.stop)
        self.input = mock.patch("builtins.input", return_value="")
        self.mock_input = self.input.start()
        self.addCleanup(self.input.stop)
        self.sub = mock.patch.object(connect_cli.subprocess, "run")
        self.mock_sub = self.sub.start()
        self.mock_sub.return_value = mock.Mock(returncode=0, stderr="")
        self.addCleanup(self.sub.stop)

    def run_wrapper(self):
        with redirect_stderr(io.StringIO()) as err:
            code = connect_cli.run(self.conn.id)
        return code, err.getvalue()

    def test_normal_session_closes_without_waiting(self):
        self.run_ssh.return_value = (0, "")
        self.assertEqual(self.run_wrapper()[0], 0)
        self.mock_input.assert_not_called()

    def test_remote_command_exit_code_is_passed_on_without_waiting(self):
        self.run_ssh.return_value = (3, "")
        self.assertEqual(self.run_wrapper()[0], 3)
        self.mock_input.assert_not_called()

    def test_connection_error_keeps_window_open(self):
        self.run_ssh.return_value = (255, "ssh: connect to host 10.0.0.5 port 22: Connection timed out\n")
        code, err = self.run_wrapper()
        self.assertEqual(code, 255)
        self.assertIn("10.0.0.5", err)
        self.assertEqual(self.mock_input.call_count, 1)  # „Enter zum Schließen“
        self.mock_sub.assert_not_called()

    def test_changed_host_key_removed_after_yes_and_retried_once(self):
        self.run_ssh.side_effect = [(255, CHANGED), (0, "")]
        self.mock_input.return_value = "j"
        code, _ = self.run_wrapper()
        self.assertEqual(code, 0)
        self.assertEqual(self.mock_sub.call_args.args[0], ["ssh-keygen", "-R", "10.0.0.5"])
        self.assertEqual(self.run_ssh.call_count, 2)
        self.assertEqual(self.run_ssh.call_args_list[0].args, self.run_ssh.call_args_list[1].args)

    def test_changed_host_key_kept_after_no(self):
        self.run_ssh.return_value = (255, CHANGED)
        self.mock_input.side_effect = ["n", ""]  # Rückfrage, dann „Enter zum Schließen“
        code, _ = self.run_wrapper()
        self.assertEqual(code, 255)
        self.mock_sub.assert_not_called()
        self.assertEqual(self.run_ssh.call_count, 1)

    def test_no_endless_retry_when_it_fails_again(self):
        self.run_ssh.return_value = (255, CHANGED)
        self.mock_input.side_effect = ["j", ""]
        code, _ = self.run_wrapper()
        self.assertEqual(code, 255)
        self.assertEqual(self.run_ssh.call_count, 2)

    def test_failing_ssh_keygen_does_not_retry(self):
        self.run_ssh.return_value = (255, CHANGED)
        self.mock_input.side_effect = ["j", ""]
        self.mock_sub.return_value = mock.Mock(returncode=1, stderr="known_hosts nicht schreibbar")
        code, err = self.run_wrapper()
        self.assertEqual(code, 255)
        self.assertIn("nicht schreibbar", err)
        self.assertEqual(self.run_ssh.call_count, 1)

    def test_missing_ssh_keygen_is_reported(self):
        self.run_ssh.return_value = (255, CHANGED)
        with mock.patch.object(connect_cli.shutil, "which", return_value=None):
            code, err = self.run_wrapper()
        self.assertEqual(code, 255)
        self.assertIn("ssh-keygen", err)
        self.mock_sub.assert_not_called()

    def test_missing_ssh_is_reported(self):
        self.run_ssh.side_effect = FileNotFoundError
        self.assertEqual(self.run_wrapper()[0], 1)


class LaunchViaWrapperTests(unittest.TestCase):
    def launch(self, template, conn):
        cfg = Config(terminals=[Terminal("T", template)])
        with mock.patch.object(launcher.shutil, "which", return_value="/usr/bin/x"), \
                mock.patch.object(launcher.subprocess, "Popen") as popen:
            launcher.launch(cfg, conn)
        return popen.call_args.args[0]

    def test_connection_without_password_also_uses_the_wrapper(self):
        conn = Connection("n", "h", user="u")
        argv = self.launch("xterm -e {ssh_args}", conn)
        self.assertEqual(argv[-2:], ["--connect", conn.id])

    def test_ssh_command_placeholder_uses_the_wrapper(self):
        conn = Connection("n", "h")
        self.assertIn("--connect", self.launch("cmd.exe /k {ssh_command}", conn)[-1])

    def test_terminal_without_ssh_placeholder_starts_the_connection_itself(self):
        conn = Connection("n", "h", user="u", port=2222)
        argv = self.launch("putty.exe -ssh -P {port} {destination}", conn)
        self.assertEqual(argv, ["putty.exe", "-ssh", "-P", "2222", "u@h"])


if __name__ == "__main__":
    unittest.main()


class EnsureKnownHostsTests(unittest.TestCase):
    def setUp(self):
        from sesam import connect_cli
        self.cli = connect_cli
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.kh = Path(tmp.name) / ".ssh" / "known_hosts"
        p = mock.patch.object(connect_cli, "known_hosts_file", return_value=self.kh)
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(connect_cli.shutil, "which", return_value="/usr/bin/x")
        p.start()
        self.addCleanup(p.stop)

    def fake_run(self, known=()):
        calls = []

        def run(cmd, **kw):
            calls.append(cmd)
            r = mock.Mock(returncode=0, stdout="", stderr="")
            if cmd[1] == "-F":
                r.returncode, r.stdout = (0, "found") if cmd[2] in known else (1, "")
            elif cmd[0] == "ssh-keyscan":
                r.stdout = f"# c\n{cmd[-1]} ssh-ed25519 AAAAkey\n"
            else:
                r.stdout = "256 SHA256:abc host (ED25519)\n"
            return r
        return run, calls

    def test_known_host_needs_no_question(self):
        run, calls = self.fake_run(known={"h"})
        with mock.patch.object(self.cli.subprocess, "run", run), mock.patch.object(self.cli, "_ask") as ask:
            self.assertTrue(self.cli.ensure_known_hosts(Connection("n", "h")))
        ask.assert_not_called()
        self.assertFalse(self.kh.exists())

    def test_unknown_host_is_written_only_after_confirmation(self):
        run, _ = self.fake_run()
        with mock.patch.object(self.cli.subprocess, "run", run), mock.patch.object(self.cli, "_ask", return_value=False):
            self.assertFalse(self.cli.ensure_known_hosts(Connection("n", "h")))
        self.assertFalse(self.kh.exists())
        with mock.patch.object(self.cli.subprocess, "run", run), mock.patch.object(self.cli, "_ask", return_value=True):
            self.assertTrue(self.cli.ensure_known_hosts(Connection("n", "h")))
        self.assertEqual(self.kh.read_text(encoding="utf-8"), "h ssh-ed25519 AAAAkey\n")

    def test_jump_hosts_and_ports_are_checked_too(self):
        run, calls = self.fake_run()
        conn = Connection("n", "target", port=2222, jump_host="admin@bastion:2200")
        with mock.patch.object(self.cli.subprocess, "run", run), mock.patch.object(self.cli, "_ask", return_value=True):
            self.assertTrue(self.cli.ensure_known_hosts(conn))
        self.assertEqual([c[2] for c in calls if c[1] == "-F"], ["[target]:2222", "[bastion]:2200"])

    def test_unreachable_host_aborts(self):
        def run(cmd, **kw):
            return mock.Mock(returncode=1 if cmd[1] == "-F" else 0, stdout="", stderr="")
        with mock.patch.object(self.cli.subprocess, "run", run):
            self.assertFalse(self.cli.ensure_known_hosts(Connection("n", "h")))
