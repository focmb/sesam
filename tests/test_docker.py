"""Tests für die Docker-Anbindung: ps-Ausgabe lesen, exec-Befehl bauen, Container per SSH abfragen, Start im Terminal."""
import json
import os
import shlex
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))

from sesam import connect_cli, docker, launcher
from sesam.models import Config, Connection, Terminal

try:
    import paramiko
except ImportError:  # paramiko nicht installiert
    paramiko = None


def ps_line(name, image="nginx:1.27", status="Up 2 hours", cid="abc123", ports="", state="running"):
    return json.dumps({"ID": cid, "Names": name, "Image": image, "Status": status, "Ports": ports, "State": state})


class ParseTests(unittest.TestCase):
    def test_parse_ps_reads_json_lines_sorted_by_name(self):
        out = "\n".join([ps_line("web", cid="1"), ps_line("Api", image="app:2", cid="2"), ps_line("db", image="postgres:16", cid="3")]) + "\n"
        found = docker.parse_ps(out)
        self.assertEqual([c.name for c in found], ["Api", "db", "web"])  # ohne Rücksicht auf Groß-/Kleinschreibung
        self.assertEqual((found[0].image, found[0].status, found[0].id), ("app:2", "Up 2 hours", "2"))

    def test_parse_ps_ignores_junk_empty_and_nameless_lines(self):
        out = "\n".join(["", "kein json", "[1, 2]", ps_line(""), ps_line("ok")])
        self.assertEqual([c.name for c in docker.parse_ps(out)], ["ok"])

    def test_parse_ps_takes_first_name_and_strips_slash(self):
        self.assertEqual(docker.parse_ps(ps_line("/web,/other/db"))[0].name, "web")

    def test_running_follows_state_with_status_as_fallback(self):
        up, down = docker.parse_ps(ps_line("a") + "\n" + ps_line("b", status="Exited (1) 3 days ago", state="exited"))
        self.assertTrue(up.running)
        self.assertFalse(down.running)
        self.assertTrue(docker.Container("i", "n", "img", "Up 5 minutes").running)  # ohne State (alte Docker-Version)
        self.assertFalse(docker.Container("i", "n", "img", "Exited (0) 1 hour ago").running)
        self.assertTrue(docker.Container("i", "n", "img", "Up 1 second (Paused)", state="paused").running)

    def test_parse_ps_empty_output(self):
        self.assertEqual(docker.parse_ps(""), [])

    def test_describe_error(self):
        d = docker.describe_error
        self.assertIn("nicht installiert", d(127, "bash: docker: command not found"))
        self.assertIn("nicht installiert", d(1, "sh: 1: docker: not found"))
        self.assertIn("Kein Zugriff", d(1, "permission denied while trying to connect to the Docker daemon socket at unix:///var/run/docker.sock"))
        self.assertIn("läuft nicht", d(1, "Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon running?"))
        self.assertEqual(d(2, "irgendwas\nzweite Zeile"), "Docker meldet: irgendwas")
        self.assertEqual(d(3, ""), "Docker meldet: Fehlercode 3")


class ActionCommandTests(unittest.TestCase):
    def test_simple_actions(self):
        for action in ("start", "stop", "restart"):
            self.assertEqual(docker.action_command("web", action), f"docker {action} web")

    def test_remove_forces_only_running_containers(self):
        self.assertEqual(docker.action_command(docker.Container("1", "web", "i", "Exited (0) 1 h ago", state="exited"), "remove"), "docker rm web")
        self.assertEqual(docker.action_command(docker.Container("1", "web", "i", "Up 1 h", state="running"), "remove"), "docker rm -f web")
        self.assertNotIn("-v", docker.action_command("web", "remove").split())  # Volumes bleiben

    def test_name_is_quoted_and_bad_input_rejected(self):
        self.assertEqual(shlex.split(docker.action_command("a;b", "stop")), ["docker", "stop", "a;b"])
        with self.assertRaises(docker.DockerError):
            docker.action_command("web", "kill")
        with self.assertRaises(docker.DockerError):
            docker.action_command("  ", "start")


class ExecCommandTests(unittest.TestCase):
    def test_default_shell_is_bash_with_sh_fallback(self):
        parts = shlex.split(docker.exec_command("web"))
        self.assertEqual(parts[:4], ["docker", "exec", "-it", "web"])
        self.assertEqual(parts[4:6], ["sh", "-c"])
        self.assertIn("exec bash", parts[6])
        self.assertIn("exec sh", parts[6])

    def test_user_and_custom_shell(self):
        parts = shlex.split(docker.exec_command("db", shell="/bin/zsh -l", user=" postgres "))
        self.assertEqual(parts, ["docker", "exec", "-it", "-u", "postgres", "db", "/bin/zsh", "-l"])

    def test_values_are_quoted_for_the_host_shell(self):
        cmd = docker.exec_command("a;b", user="x y")
        self.assertEqual(shlex.split(cmd)[:6], ["docker", "exec", "-it", "-u", "x y", "a;b"])
        self.assertNotIn(" a;b", cmd)  # das Semikolon steht nur in Anführungszeichen

    def test_invalid_shell_quoting_raises(self):
        with self.assertRaises(docker.DockerError):
            docker.exec_command("web", shell='bash -c "offen')

    def test_container_target_uses_connection_defaults_and_title(self):
        conn = Connection(name="Server", host="h", docker=True, docker_shell="/bin/ash", docker_user="app")
        c, cmd = docker.container_target(conn, "web")
        self.assertEqual(c.name, "Server · web")
        self.assertEqual(c.host, "h")
        self.assertEqual(shlex.split(cmd), ["docker", "exec", "-it", "-u", "app", "web", "/bin/ash"])

    def test_container_target_explicit_values_override_defaults(self):
        conn = Connection(name="Server", host="h", docker=True, docker_shell="/bin/ash", docker_user="app")
        _c, cmd = docker.container_target(conn, "web", shell="", user="")  # leer = automatisch / Standard des Containers
        parts = shlex.split(cmd)
        self.assertEqual(parts[:4], ["docker", "exec", "-it", "web"])
        self.assertNotIn("-u", parts)
        _c, cmd = docker.container_target(conn, "web", shell="bash", user="root")
        self.assertEqual(shlex.split(cmd), ["docker", "exec", "-it", "-u", "root", "web", "bash"])


class LaunchTests(unittest.TestCase):
    def setUp(self):
        self.cfg = Config(terminals=[Terminal("T", "xterm -T {title} -e {ssh_args}")])
        self.conn = Connection(name="Server", host="h.example", user="u", sudo_mode="login", remote_command="tmux attach")

    def launch(self, conn, command):
        with mock.patch.object(launcher.shutil, "which", return_value="/usr/bin/xterm"), \
                mock.patch.object(launcher.subprocess, "Popen") as popen:
            launcher.launch(self.cfg, conn, remote_command=command)
        return popen.call_args.args[0]

    def test_remote_command_replaces_stored_command_and_drops_host_sudo(self):
        c, cmd = docker.container_target(self.conn, "web")
        argv = self.launch(c, cmd)
        self.assertEqual(argv[:4], ["xterm", "-T", "Server · web", "-e"])
        ssh = argv[4:]
        self.assertEqual(ssh[0], "ssh")
        self.assertIn("-t", ssh)
        self.assertEqual(ssh[-2:], ["u@h.example", cmd])
        self.assertNotIn("sudo", " ".join(ssh))
        self.assertNotIn("tmux", " ".join(ssh))

    def test_without_remote_command_nothing_changes(self):
        argv = self.launch(self.conn, None)
        self.assertEqual(argv[-1], "sudo -i tmux attach")

    def test_saved_password_goes_through_wrapper_with_override(self):
        conn = replace(self.conn, use_password=True)
        c, cmd = docker.container_target(conn, "web")
        argv = self.launch(c, cmd)
        self.assertIn("--connect", argv)
        i = argv.index("--remote-command")
        self.assertEqual(argv[i + 1], cmd)
        self.assertNotIn("--sudo-mode", argv)

    def test_wrapper_parses_override_and_switches_host_sudo_off(self):
        cmd = docker.exec_command("web")
        overrides = connect_cli.parse_overrides(["--remote-command", cmd])
        self.assertEqual(overrides, {"remote_command": cmd, "sudo_mode": "", "sudo_user": ""})
        conn = replace(self.conn, **overrides)
        args, _env = connect_cli.prepare(conn, None, "")
        self.assertEqual(args[-2:], ["u@h.example", cmd])
        self.assertEqual(connect_cli.parse_overrides(["--sudo-mode", "shell"]), {"sudo_mode": "shell"})  # bisheriges Verhalten


@unittest.skipIf(paramiko is None, "paramiko nicht verfügbar")
class ListContainersTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.host_key = paramiko.RSAKey.generate(2048)

    def setUp(self):
        from sftp_server import SftpTestServer

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.reply = ((ps_line("web") + "\n" + ps_line("db", image="postgres:16")).encode(), b"", 0)
        self.server = SftpTestServer(str(self.base), host_key=self.host_key, exec_handler=lambda cmd: self.reply)
        self.addCleanup(self.server.close)
        self.cfg = Config()
        self.conn = Connection(name="Test", host="127.0.0.1", port=self.server.port, user="tester", docker=True)

    def list(self, password="geheim", conn=None):
        return docker.list_containers(self.cfg, conn or self.conn, password=password, known_hosts=self.base / "known_hosts")

    def test_lists_running_containers(self):
        found = self.list()
        self.assertEqual([(c.name, c.image) for c in found], [("db", "postgres:16"), ("web", "nginx:1.27")])
        self.assertEqual(self.server.commands, [docker.LIST_COMMAND])

    def test_no_containers_gives_empty_list(self):
        self.reply = (b"", b"", 0)
        self.assertEqual(self.list(), [])

    def test_error_codes_become_readable_messages(self):
        self.reply = (b"", b"bash: docker: command not found\n", 127)
        with self.assertRaises(docker.DockerError) as ctx:
            self.list()
        self.assertIn("nicht installiert", str(ctx.exception))
        self.reply = (b"", b"permission denied while trying to connect to the Docker daemon socket\n", 1)
        with self.assertRaises(docker.DockerError) as ctx:
            self.list()
        self.assertIn("Kein Zugriff", str(ctx.exception))

    def test_run_action_sends_command_and_reports_errors(self):
        box = docker.Container("1", "web", "nginx", "Exited (0) 1 hour ago", state="exited")
        docker.run_action(self.cfg, self.conn, box, "start", password="geheim", known_hosts=self.base / "known_hosts")
        self.assertEqual(self.server.commands, ["docker start web"])
        self.reply = (b"", b"Error response from daemon: No such container: web\n", 1)
        with self.assertRaises(docker.DockerError) as ctx:
            docker.run_action(self.cfg, self.conn, box, "start", password="geheim", known_hosts=self.base / "known_hosts")
        self.assertIn("No such container", str(ctx.exception))

    def test_wrong_password_raises_auth_error(self):
        from sesam import sftp

        with self.assertRaises(sftp.SftpAuthError):
            self.list(password="falsch")

    def test_jump_host_is_reported_for_container_view(self):
        from sesam import sftp

        conn = replace(self.conn, jump_host="a@b")
        with self.assertRaises(sftp.SftpError) as ctx:
            self.list(conn=conn)
        self.assertIn("Container-Anzeige", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
