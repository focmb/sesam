"""Snippets: Datenmodell, Ausführung im Terminal, Synchronisation."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))

from sesam import launcher, snippets, storage
from sesam.models import Config, Connection, Snippet, Terminal
from ssh_helper import wrapped_ssh


class ModelTests(unittest.TestCase):
    def test_from_dict_cleans_bad_values(self):
        s = Snippet.from_dict({"name": "x", "command": 5, "after": "boom", "sudo": "ja", "category": None, "use_password": True})
        self.assertEqual((s.command, s.after, s.sudo, s.category, s.use_password), ("", "shell", False, "", False))
        self.assertEqual(Snippet.from_dict({"name": "a", "unknown": 1}).name, "a")

    def test_roundtrip_in_config_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.json"
            storage.save(Config(snippets=[Snippet("Disk", command="df -h", category="System/Disk", sudo=True, after="wait")]), path)
            (s,) = storage.load(path).snippets
            self.assertEqual((s.name, s.command, s.category, s.sudo, s.after), ("Disk", "df -h", "System/Disk", True, "wait"))
            self.assertIs(storage.load(path).snippet_by_id(s.id).id == s.id, True)

    def test_broken_snippet_list_does_not_break_loading(self):
        self.assertEqual(storage._from_data({"snippets": "kaputt"}).snippets, [])
        self.assertEqual(storage._from_data({"snippets": [1, None, {"name": "ok"}]}).snippets[0].name, "ok")

    def test_categories_include_parents_and_filtering(self):
        cfg = Config(snippets=[Snippet("a", category="Docker/Logs"), Snippet("b", category="Docker"), Snippet("c"), Snippet("d", category=" Docker//Logs ")])
        self.assertEqual(snippets.categories(cfg), ["Docker", "Docker/Logs"])
        self.assertEqual([s.name for s in snippets.in_category(cfg, "Docker/Logs")], ["a", "d"])
        self.assertEqual([s.name for s in snippets.in_category(cfg, "")], ["c"])


class CommandTests(unittest.TestCase):
    @unittest.skipIf(shutil.which("sh") is None, "sh nicht verfügbar")
    def test_command_really_runs_in_a_shell_for_all_modes(self):
        for after in ("shell", "wait", "close"):
            line = snippets.remote_command(Snippet("x", command="echo eins && echo 'zwei  drei'\necho \"$((1+2))\"", after=after))
            if after == "shell":
                line = line.replace('exec "${SHELL:-sh}" -l', "echo ENDE")
            if after == "wait":
                line = line.split("; printf")[0]
            out = subprocess.run(["sh", "-c", line], capture_output=True, text=True).stdout
            self.assertEqual(out.split("\n")[:3], ["eins", "zwei  drei", "3"], after)
            if after == "shell":
                self.assertIn("ENDE", out)

    def test_after_variants(self):
        base = snippets.remote_command(Snippet("x", command="ls", after="close"))
        self.assertEqual(base, "sh -c ls")
        self.assertIn('exec "${SHELL:-sh}" -l', snippets.remote_command(Snippet("x", command="ls", after="shell")))
        self.assertIn("read -r", snippets.remote_command(Snippet("x", command="ls", after="wait")))

    @unittest.skipIf(shutil.which("sh") is None or sys.platform.startswith("win"),
                     "braucht eine POSIX-sh (unter Windows andere Pfadschreibweise)")
    def test_hostile_text_stays_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            evil = Snippet("x", command=f"echo 'a'; echo \"it's\" > {tmp}/out", after="close")
            subprocess.run(["sh", "-c", snippets.remote_command(evil)], capture_output=True)
            self.assertEqual((Path(tmp) / "out").read_text(), "it's\n")


class WindowsTerminalTests(unittest.TestCase):
    """Windows Terminal (wt.exe) zerlegt die Befehlszeile an „;“, auch in Anführungszeichen: Snippets mit „;“ schlugen dort fehl."""

    def setUp(self):
        self.conn = Connection(name="Server; eins", host="h.example", user="u")
        self.line = snippets.remote_command(Snippet(name="s", command="uptime", after="shell"))
        self.assertIn(";", self.line)

    def build(self, command, ssh_args):
        return launcher.build_terminal_command(Terminal("T", command), self.conn, ssh_args)

    def test_semicolons_are_escaped_for_windows_terminal(self):
        ssh = ["ssh", "-t", "u@h.example", self.line]
        argv = self.build("wt.exe new-tab --title {title} {ssh_args}", ssh)
        self.assertEqual(argv[:4], ["wt.exe", "new-tab", "--title", "Server\\; eins"])
        self.assertEqual(argv[-1], self.line.replace(";", "\\;"))
        self.assertNotIn(";", argv[-1].replace("\\;", ""))  # kein ungeschütztes Semikolon

    def test_path_to_wt_is_recognised_and_wrapper_args_are_escaped_too(self):
        wrapper = ["python", "-m", "sesam", "--connect", "id", "--remote-command", self.line]
        with mock.patch.object(launcher, "IS_WINDOWS", True):  # Windows-Pfade mit „\\“ zerlegt nur die Windows-Variante richtig
            argv = self.build(r"C:\Users\x\AppData\Local\Microsoft\WindowsApps\wt.exe new-tab {ssh_args}", wrapper)
        self.assertEqual(argv[-1], self.line.replace(";", "\\;"))

    def test_other_terminals_and_template_separators_stay_untouched(self):
        ssh = ["ssh", "-t", "u@h.example", self.line]
        self.assertEqual(self.build("xterm -e {ssh_args}", ssh)[-1], self.line)
        self.assertEqual(self.build("xterm -T {title} -e {ssh_args}", ssh)[2], "Server; eins")
        argv = self.build("wt.exe new-tab {ssh_args} ; split-pane {ssh_args}", ssh)  # eigenes „;“ der Vorlage bleibt Trenner
        self.assertIn(";", argv)


class RunTests(unittest.TestCase):
    def setUp(self):
        self.cfg = Config(terminals=[Terminal("T", "xterm -e {ssh_args}")])
        self.cfg.default_terminal_id = self.cfg.terminals[0].id
        self.a = Connection("a", "a.example", user="u")
        self.b = Connection("b", "b.example", port=2222, sudo_mode="shell", sudo_user="admin")
        self.cfg.connections += [self.a, self.b]
        patcher = mock.patch.object(launcher.shutil, "which", lambda name: "/usr/bin/" + name)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.popen = mock.patch.object(launcher.subprocess, "Popen")
        self.mock_popen = self.popen.start()
        self.addCleanup(self.popen.stop)

    def commands(self):
        return [c.args[0] for c in self.mock_popen.call_args_list]

    def test_starts_one_terminal_per_connection_with_the_command(self):
        sn = Snippet("Disk", command="df -h", after="close")
        rep = snippets.run(self.cfg, sn, [self.a, self.b])
        self.assertEqual((rep.started, rep.errors), (["a", "b"], []))
        first, second = self.commands()
        self.assertEqual(first[:2], ["xterm", "-e"])
        ssh_a, ssh_b = wrapped_ssh(first, self.a), wrapped_ssh(second, self.b)
        self.assertIn("u@a.example", ssh_a)
        self.assertEqual(ssh_a[-1], "sh -c 'df -h'")
        self.assertIn("-t", ssh_a)
        self.assertIn("2222", ssh_b)
        self.assertEqual(ssh_b[-1], "sh -c 'df -h'")  # ohne sudo: das sudo der Verbindung gilt nicht

    def test_sudo_snippet_runs_sudo_only_around_the_command(self):
        sn = Snippet("Dienst", command="systemctl restart nginx && echo ok", sudo=True, after="shell")
        snippets.run(self.cfg, sn, [self.a, self.b])
        first, second = self.commands()
        self.assertEqual(wrapped_ssh(first, self.a)[-1], "sudo -i sh -c 'systemctl restart nginx && echo ok'; exec \"${SHELL:-sh}\" -l")
        self.assertEqual(wrapped_ssh(second, self.b)[-1], "sudo -u admin -i sh -c 'systemctl restart nginx && echo ok'; exec \"${SHELL:-sh}\" -l")

    def test_stored_password_is_used_via_wrapper_for_connections_with_password(self):
        self.a.use_password = True
        snippets.run(self.cfg, Snippet("x", command="id", sudo=True, after="close"), [self.a])
        (cmd,) = self.commands()
        self.assertIn("--connect", cmd)
        self.assertEqual(cmd[cmd.index("--remote-command") + 1], "sh -c id")
        self.assertEqual(cmd[cmd.index("--sudo-mode") + 1], "login")

    def test_error_on_one_target_does_not_stop_the_others(self):
        self.a.key_id = "weg"  # existiert nicht mehr
        rep = snippets.run(self.cfg, Snippet("x", command="id"), [self.a, self.b])
        self.assertEqual(rep.started, ["b"])
        self.assertEqual(rep.errors[0][0], "a")
        self.assertIn("Schlüssel", rep.errors[0][1])
        self.assertEqual(self.mock_popen.call_count, 1)

    def test_empty_command_starts_nothing(self):
        rep = snippets.run(self.cfg, Snippet("x", command="  \n "), [self.a])
        self.assertEqual((rep.started, len(rep.errors)), ([], 1))
        self.mock_popen.assert_not_called()

    def test_connection_remote_command_and_sudo_of_the_host_are_replaced(self):
        self.b.remote_command = "tmux attach"
        snippets.run(self.cfg, Snippet("x", command="id", after="close"), [self.b])
        self.assertNotIn("tmux", wrapped_ssh(self.commands()[0], self.b)[-1])

    def test_launch_without_sudo_flag_keeps_old_behaviour_for_docker(self):
        launcher.launch(self.cfg, self.b, remote_command="docker exec -it c bash")
        self.assertEqual(wrapped_ssh(self.commands()[0], self.b)[-1], "docker exec -it c bash")


class SyncTests(unittest.TestCase):
    def setUp(self):
        import test_sync

        self.h = test_sync.EngineTests("test_first_device_pushes_encrypted_and_second_device_receives")
        self.h.setUp()
        self.addCleanup(self.h.tmp.cleanup)

    def test_snippets_sync_in_clear_text_and_deletions_propagate(self):
        cfgA, A, _ = self.h.device("A")
        sn = Snippet("Disk", command="df -h", category="System", sudo=True, after="wait")
        cfgA.snippets.append(sn)
        A.run(cfgA)
        files = self.h.remote_text()
        self.assertIn(f"snippets/{sn.id}.json", files)
        self.assertIn("df -h", files[f"snippets/{sn.id}.json"])
        self.assertNotIn("use_password", files[f"snippets/{sn.id}.json"])

        cfgB, B, _ = self.h.device("B")
        B.run(cfgB)
        (got,) = cfgB.snippets
        self.assertEqual((got.name, got.command, got.category, got.sudo, got.after), ("Disk", "df -h", "System", True, "wait"))

        got.command = "df -hT"
        B.run(cfgB)
        A.run(cfgA)
        self.assertEqual(cfgA.snippets[0].command, "df -hT")
        cfgA.snippets.clear()
        A.run(cfgA)
        B.run(cfgB)
        self.assertEqual(cfgB.snippets, [])

    def test_snippets_sync_without_password_sync_too(self):
        cfgA, A, _ = self.h.device("A", passwords_on=False)
        cfgA.snippets.append(Snippet("Uptime", command="uptime"))
        A.run(cfgA)
        cfgB, B, _ = self.h.device("B", passwords_on=False)
        B.run(cfgB)
        self.assertEqual([s.name for s in cfgB.snippets], ["Uptime"])


if __name__ == "__main__":
    unittest.main()
