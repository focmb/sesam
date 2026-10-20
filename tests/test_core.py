import tempfile
import unittest
from pathlib import Path

from ssh_verwaltung import launcher, storage
from ssh_verwaltung.models import Config, Connection, Terminal
from ssh_verwaltung.ssh_config import parse_ssh_config


class LauncherTests(unittest.TestCase):
    def test_ssh_args_basic(self):
        c = Connection(name="a", host="h.example", user="root", port=2222, identity_file="/k", jump_host="j@b", extra_args="-o A=B")
        self.assertEqual(launcher.build_ssh_args(c), ["ssh", "-p", "2222", "-i", "/k", "-J", "j@b", "-o", "A=B", "root@h.example"])

    def test_ssh_args_remote_command(self):
        c = Connection(name="a", host="h", remote_command="tmux attach")
        self.assertEqual(launcher.build_ssh_args(c), ["ssh", "-t", "h", "tmux attach"])

    def test_terminal_ssh_args_expansion(self):
        t = Terminal("x", "gnome-terminal --title {title} -- {ssh_args}")
        c = Connection(name="Mein Server", host="h", user="u")
        self.assertEqual(launcher.build_terminal_command(t, c), ["gnome-terminal", "--title", "Mein Server", "--", "ssh", "u@h"])

    def test_terminal_ssh_command_string(self):
        t = Terminal("x", "cmd.exe /k {ssh_command}")
        c = Connection(name="n", host="h", user="u", port=2200)
        out = launcher.build_terminal_command(t, c)
        self.assertEqual(out[:2], ["cmd.exe", "/k"])
        self.assertIn("-p 2200", out[2])

    def test_resolve_terminal_prefers_connection_then_default(self):
        t1, t2 = Terminal("a", "a"), Terminal("b", "b")
        cfg = Config(terminals=[t1, t2], default_terminal_id=t2.id)
        self.assertIs(launcher.resolve_terminal(cfg, Connection("c", "h")), t2)
        self.assertIs(launcher.resolve_terminal(cfg, Connection("c", "h", terminal_id=t1.id)), t1)

    def test_no_terminal_raises(self):
        with self.assertRaises(launcher.LaunchError):
            launcher.resolve_terminal(Config(), Connection("c", "h"))

    def test_missing_program_raises(self):
        t = Terminal("x", "definitiv-nicht-vorhanden {ssh_args}")
        cfg = Config(terminals=[t], default_terminal_id=t.id)
        with self.assertRaises(launcher.LaunchError):
            launcher.launch(cfg, Connection("c", "h"))


class StorageTests(unittest.TestCase):
    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "sub" / "config.json"
            t = Terminal("T", "xterm -e {ssh_args}")
            cfg = Config(terminals=[t], connections=[Connection("n", "h", group="g")], default_terminal_id=t.id)
            storage.save(cfg, p)
            loaded = storage.load(p)
            self.assertEqual(loaded, cfg)

    def test_first_start_has_terminals(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = storage.load(Path(d) / "none.json")
            self.assertTrue(cfg.terminals)
            self.assertTrue(cfg.terminal_by_id(cfg.default_terminal_id))


class SshConfigTests(unittest.TestCase):
    def test_parse(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config"
            p.write_text("Host *\n  User x\n\nHost web db\n  HostName 10.0.0.5\n  User admin\n  Port 2222\n  ProxyJump bastion\n")
            conns = parse_ssh_config(p)
            self.assertEqual([c.name for c in conns], ["web", "db"])
            self.assertEqual(conns[0].host, "10.0.0.5")
            self.assertEqual(conns[0].port, 2222)
            self.assertEqual(conns[0].jump_host, "bastion")


if __name__ == "__main__":
    unittest.main()


class PasswordTests(unittest.TestCase):
    def test_wrapper_args_used_when_password(self):
        from ssh_verwaltung.launcher import wrapper_args
        c = Connection("n", "h", use_password=True)
        w = wrapper_args(c)
        self.assertEqual(w[-2:], ["--connect", c.id])
        t = Terminal("x", "gnome-terminal -- {ssh_args}")
        self.assertEqual(launcher.build_terminal_command(t, c, w)[-2:], ["--connect", c.id])

    def test_prepare_with_password(self):
        from ssh_verwaltung.connect_cli import prepare
        c = Connection("n", "h", user="u")
        args, env = prepare(c, "geheim", "/x/askpass.sh", {})
        self.assertEqual(args, ["ssh", "-o", "StrictHostKeyChecking=accept-new", "u@h"])
        self.assertEqual(env["SSHV_PASSWORD"], "geheim")
        self.assertEqual(env["SSH_ASKPASS_REQUIRE"], "force")
        self.assertEqual(env["SSH_ASKPASS"], "/x/askpass.sh")

    def test_prepare_respects_user_hostkey_option(self):
        from ssh_verwaltung.connect_cli import prepare
        c = Connection("n", "h", extra_args="-o StrictHostKeyChecking=yes")
        args, _ = prepare(c, "pw", "/a", {})
        self.assertNotIn("StrictHostKeyChecking=accept-new", args)

    def test_prepare_without_password_leaves_env(self):
        from ssh_verwaltung.connect_cli import prepare
        _, env = prepare(Connection("n", "h"), None, "", {"A": "1"})
        self.assertEqual(env, {"A": "1"})

    def test_askpass_answers_only_password_prompts(self):
        from ssh_verwaltung.askpass import answer
        env = {"SSHV_PASSWORD": "pw"}
        self.assertEqual(answer("u@h's password: ", env), "pw")
        self.assertIsNone(answer("Are you sure you want to continue connecting (yes/no)? ", env))
        self.assertIsNone(answer("Enter passphrase for key '/k': ", env))

    def test_askpass_script_is_executable_and_correct(self):
        import os, subprocess, tempfile
        from unittest import mock
        from ssh_verwaltung import askpass
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(askpass.storage, "config_dir", return_value=Path(d)):
                script = askpass.askpass_path()
            env = {**os.environ, "SSHV_PASSWORD": "p&ss $x'\"w"}
            r = subprocess.run([script, "user@h's password: "], env=env, capture_output=True, text=True)
            self.assertEqual(r.stdout, "p&ss $x'\"w\n")
            r = subprocess.run([script, "Are you sure (yes/no)?"], env=env, capture_output=True, text=True)
            self.assertEqual(r.returncode, 1)


class TemplateTests(unittest.TestCase):
    def test_ghostty_in_linux_templates(self):
        from unittest import mock
        from ssh_verwaltung import terminals
        with mock.patch.object(terminals, "current_platform", return_value="linux"):
            names = [t.name for t in terminals.template_terminals()]
        self.assertIn("Ghostty", names)

    def test_ghostty_command(self):
        t = Terminal("G", "ghostty --title={title} -e {ssh_args}")
        c = Connection("Srv", "h", user="u")
        self.assertEqual(launcher.build_terminal_command(t, c), ["ghostty", "--title=Srv", "-e", "ssh", "u@h"])


class GroupTests(unittest.TestCase):
    def _cfg(self):
        return Config(
            connections=[
                Connection("a", "h", group="Kunde/Prod"),
                Connection("b", "h", group="Kunde"),
                Connection("c", "h", group="Kunde/Prod/DB"),
                Connection("d", "h"),
            ],
            groups=["Leer"],
        )

    def test_all_groups_includes_ancestors_and_empty(self):
        from ssh_verwaltung import groups as g
        self.assertEqual(g.all_groups(self._cfg()), ["Kunde", "Kunde/Prod", "Kunde/Prod/DB", "Leer"])

    def test_normalize(self):
        from ssh_verwaltung import groups as g
        self.assertEqual(g.normalize(" A / /B\\C/ "), "A/B/C")
        self.assertEqual(g.normalize("  "), "")

    def test_rename_moves_subgroups_and_connections(self):
        from ssh_verwaltung import groups as g
        cfg = self._cfg()
        g.rename_group(cfg, "Kunde/Prod", "Archiv/Alt")
        self.assertEqual([c.group for c in cfg.connections], ["Archiv/Alt", "Kunde", "Archiv/Alt/DB", ""])

    def test_rename_into_itself_rejected(self):
        from ssh_verwaltung import groups as g
        with self.assertRaises(ValueError):
            g.rename_group(self._cfg(), "Kunde", "Kunde/Prod/Neu")

    def test_delete_moves_content_up(self):
        from ssh_verwaltung import groups as g
        cfg = self._cfg()
        g.delete_group(cfg, "Kunde/Prod")
        self.assertEqual([c.group for c in cfg.connections], ["Kunde", "Kunde", "Kunde/DB", ""])
        g.delete_group(cfg, "Leer")
        self.assertNotIn("Leer", g.all_groups(cfg))

    def test_add_group_and_roundtrip(self):
        from ssh_verwaltung import groups as g
        cfg = Config()
        g.add_group(cfg, "X / Y")
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "c.json"
            storage.save(cfg, p)
            self.assertEqual(storage.load(p).groups, ["X/Y"])
