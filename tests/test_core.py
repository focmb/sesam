import sys
import tempfile
import unittest
from pathlib import Path

from sesam import launcher, storage
from sesam.models import Config, Connection, Terminal
from sesam.ssh_config import parse_ssh_config


class LauncherTests(unittest.TestCase):
    def test_ssh_args_basic(self):
        c = Connection(name="a", host="h.example", user="root", port=2222, identity_file="/k", jump_host="j@b", extra_args="-o A=B")
        self.assertEqual(launcher.build_ssh_args(c), ["ssh", "-p", "2222", "-i", "/k", "-J", "j@b", "-o", "A=B", "--", "root@h.example"])

    def test_ssh_args_remote_command(self):
        c = Connection(name="a", host="h", remote_command="tmux attach")
        self.assertEqual(launcher.build_ssh_args(c), ["ssh", "-t", "--", "h", "tmux attach"])

    def test_terminal_ssh_args_expansion(self):
        t = Terminal("x", "gnome-terminal --title {title} -- {ssh_args}")
        c = Connection(name="Mein Server", host="h", user="u")
        self.assertEqual(launcher.build_terminal_command(t, c), ["gnome-terminal", "--title", "Mein Server", "--", "ssh", "--", "u@h"])

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
        from sesam.launcher import wrapper_args
        c = Connection("n", "h", use_password=True)
        w = wrapper_args(c)
        self.assertEqual(w[-2:], ["--connect", c.id])
        t = Terminal("x", "gnome-terminal -- {ssh_args}")
        self.assertEqual(launcher.build_terminal_command(t, c, w)[-2:], ["--connect", c.id])

    def test_prepare_with_password(self):
        from sesam.connect_cli import prepare
        c = Connection("n", "h", user="u")
        args, env = prepare(c, "geheim", "/x/askpass.sh", {})
        self.assertEqual(args, ["ssh", "--", "u@h"])
        self.assertEqual(env["SSHV_PASSWORD"], "geheim")
        self.assertEqual(env["SSH_ASKPASS_REQUIRE"], "force")
        self.assertEqual(env["SSH_ASKPASS"], "/x/askpass.sh")
        self.assertEqual(env["SSHV_HOST"], c.host)

    def test_prepare_respects_user_hostkey_option(self):
        from sesam.connect_cli import prepare
        c = Connection("n", "h", extra_args="-o StrictHostKeyChecking=yes")
        args, _ = prepare(c, "pw", "/a", {})
        self.assertNotIn("StrictHostKeyChecking=accept-new", args)

    def test_prepare_without_password_leaves_env(self):
        from sesam.connect_cli import prepare
        _, env = prepare(Connection("n", "h"), None, "", {"A": "1"})
        self.assertEqual(env, {"A": "1"})

    def test_askpass_answers_only_password_prompts(self):
        from sesam.askpass import answer
        env = {"SSHV_PASSWORD": "pw", "SSHV_HOST": "h"}
        self.assertEqual(answer("u@h's password: ", env), "pw")
        self.assertEqual(answer("(u@H) Password: ", env), "pw")                 # Keyboard-Interactive
        self.assertIsNone(answer("admin@bastion's password: ", env))             # Jump-Host
        self.assertIsNone(answer("Password: ", env))                             # vom Server formulierte Abfrage
        self.assertIsNone(answer("u@h's password: ", {"SSHV_PASSWORD": "pw"}))   # kein Zielhost bekannt
        self.assertIsNone(answer("Are you sure you want to continue connecting (yes/no)? ", env))
        self.assertIsNone(answer("Enter passphrase for key '/k': ", env))

    def test_askpass_script_is_executable_and_correct(self):
        import os, subprocess, tempfile
        from unittest import mock
        from sesam import askpass
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(askpass.storage, "config_dir", return_value=Path(d)):
                script = askpass.askpass_path()
            env = {**os.environ, "SSHV_PASSWORD": "p&ss $x'\"w", "SSHV_HOST": "h"}
            r = subprocess.run([script, "user@h's password: "], env=env, capture_output=True, text=True)
            self.assertEqual(r.stdout, "p&ss $x'\"w\n")
            for prompt in ("Are you sure (yes/no)?", "admin@bastion's password: ", "Password: "):
                r = subprocess.run([script, prompt], env=env, capture_output=True, text=True)
                self.assertEqual((r.returncode, r.stdout), (1, ""), prompt)
            r = subprocess.run([script, "(u@h) Password: "], env=env, capture_output=True, text=True)
            self.assertEqual(r.stdout, "p&ss $x'\"w\n")


class TemplateTests(unittest.TestCase):
    def test_ghostty_in_linux_templates(self):
        from unittest import mock
        from sesam import terminals
        with mock.patch.object(terminals, "current_platform", return_value="linux"):
            names = [t.name for t in terminals.template_terminals()]
        self.assertIn("Ghostty", names)

    def test_ghostty_command(self):
        t = Terminal("G", "ghostty --title={title} -e {ssh_args}")
        c = Connection("Srv", "h", user="u")
        self.assertEqual(launcher.build_terminal_command(t, c), ["ghostty", "--title=Srv", "-e", "ssh", "--", "u@h"])


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
        from sesam import groups as g
        self.assertEqual(g.all_groups(self._cfg()), ["Kunde", "Kunde/Prod", "Kunde/Prod/DB", "Leer"])

    def test_normalize(self):
        from sesam import groups as g
        self.assertEqual(g.normalize(" A / /B\\C/ "), "A/B/C")
        self.assertEqual(g.normalize("  "), "")

    def test_rename_moves_subgroups_and_connections(self):
        from sesam import groups as g
        cfg = self._cfg()
        g.rename_group(cfg, "Kunde/Prod", "Archiv/Alt")
        self.assertEqual([c.group for c in cfg.connections], ["Archiv/Alt", "Kunde", "Archiv/Alt/DB", ""])

    def test_rename_into_itself_rejected(self):
        from sesam import groups as g
        with self.assertRaises(ValueError):
            g.rename_group(self._cfg(), "Kunde", "Kunde/Prod/Neu")

    def test_delete_moves_content_up(self):
        from sesam import groups as g
        cfg = self._cfg()
        g.delete_group(cfg, "Kunde/Prod")
        self.assertEqual([c.group for c in cfg.connections], ["Kunde", "Kunde", "Kunde/DB", ""])
        g.delete_group(cfg, "Leer")
        self.assertNotIn("Leer", g.all_groups(cfg))

    def test_add_group_and_roundtrip(self):
        from sesam import groups as g
        cfg = Config()
        g.add_group(cfg, "X / Y")
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "c.json"
            storage.save(cfg, p)
            self.assertEqual(storage.load(p).groups, ["X/Y"])


class SudoTests(unittest.TestCase):
    def test_sudo_login(self):
        c = Connection("n", "h", user="u", sudo_mode="login")
        self.assertEqual(launcher.build_ssh_args(c), ["ssh", "-t", "--", "u@h", "sudo -i"])

    def test_sudo_shell_other_user_with_command(self):
        c = Connection("n", "h", sudo_mode="shell", sudo_user="www data", remote_command="tmux attach")
        self.assertEqual(launcher.build_ssh_args(c), ["ssh", "-t", "--", "h", "sudo -u 'www data' -s tmux attach"])

    def test_no_sudo_unchanged(self):
        self.assertEqual(launcher.build_ssh_args(Connection("n", "h", user="u")), ["ssh", "--", "u@h"])

    def test_wrapper_carries_sudo_override(self):
        from sesam.connect_cli import parse_overrides
        c = Connection("n", "h", use_password=True, sudo_mode="login", sudo_user="")
        w = launcher.wrapper_args(c)
        self.assertEqual(parse_overrides(w), {"sudo_mode": "login", "sudo_user": ""})
        self.assertEqual(parse_overrides(["--connect", "x"]), {})

    def test_roundtrip_fields(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "c.json"
            cfg = Config(connections=[Connection("n", "h", sudo_mode="shell", sudo_user="app")])
            storage.save(cfg, p)
            c = storage.load(p).connections[0]
            self.assertEqual((c.sudo_mode, c.sudo_user), ("shell", "app"))


class SudoPasswordTests(unittest.TestCase):
    def _conn(self, **kw):
        return Connection("n", "h", user="u", use_password=True, sudo_mode="login", **kw)

    def test_password_not_in_arguments_but_in_env(self):
        from sesam.connect_cli import prepare
        args, env = prepare(self._conn(), "geh&eim'1", "/a", {})
        self.assertNotIn("geh&eim'1", " ".join(args))
        self.assertEqual(env["LC_SSHV_PW"], "geh&eim'1")
        self.assertIn("SendEnv=LC_SSHV_PW", args)
        self.assertTrue(args[-1].endswith("env -u LC_SSHV_PW sudo -i"))

    def test_can_be_disabled_and_needs_password(self):
        from sesam.connect_cli import prepare
        args, env = prepare(self._conn(sudo_use_password=False), "pw", "/a", {})
        self.assertNotIn("LC_SSHV_PW", env)
        self.assertEqual(args[-1], "sudo -i")
        args, env = prepare(self._conn(), None, "", {})
        self.assertEqual(args[-1], "sudo -i")

    def test_remote_script_flow_with_fake_sudo(self):
        """Führt den Remote-Befehl mit einem Fake-sudo in /bin/sh aus."""
        import os, stat, subprocess, tempfile
        from sesam.connect_cli import prepare
        with tempfile.TemporaryDirectory() as d:
            sudo = Path(d) / "sudo"
            sudo.write_text(
                "#!/bin/sh\n"
                'case "$*" in\n'
                "  *-S*) read pw; [ \"$pw\" = \"$EXPECT\" ] && echo VALIDATED || echo WRONG ;;\n"
                '  *) echo "SESSION args=$* leak=${LC_SSHV_PW:-none}" ;;\n'
                "esac\n"
            )
            sudo.chmod(sudo.stat().st_mode | stat.S_IXUSR)
            conn = self._conn(remote_command="tmux attach")
            base = {"PATH": f"{d}:{os.environ['PATH']}", "EXPECT": "p w'&$x"}

            args, env = prepare(conn, "p w'&$x", "/a", base)
            out = subprocess.run(["sh", "-c", args[-1]], env=env, capture_output=True, text=True).stdout
            self.assertEqual(out.split(), ["VALIDATED", "SESSION", "args=-i", "tmux", "attach", "leak=none"])

            env_wrong = dict(env, LC_SSHV_PW="falsch")
            out = subprocess.run(["sh", "-c", args[-1]], env=env_wrong, capture_output=True, text=True).stdout
            self.assertIn("WRONG", out)          # genau ein Versuch
            self.assertIn("SESSION", out)        # danach normaler Ablauf

            env_none = {k: v for k, v in env.items() if k != "LC_SSHV_PW"}
            out = subprocess.run(["sh", "-c", args[-1]], env=env_none, capture_output=True, text=True).stdout
            self.assertNotIn("WRONG", out)       # ohne Variable kein Versuch (kein Fehlversuch im Log)
            self.assertNotIn("VALIDATED", out)


class IdentityTests(unittest.TestCase):
    def _cfg(self):
        from sesam.models import Identity
        ident = Identity("deploy", user="deploy", identity_file="/k/id", use_password=True)
        conn = Connection("srv", "h", user="alt", identity_file="/alt", use_password=True, identity_id=ident.id)
        return Config(identities=[ident], connections=[conn]), ident, conn

    def test_identity_overrides_credentials(self):
        cfg, ident, conn = self._cfg()
        eff, key = launcher.resolve(cfg, conn)
        self.assertEqual((eff.user, eff.identity_file, eff.use_password), ("deploy", "/k/id", True))
        self.assertEqual(key, ident.id)          # Passwort liegt unter der Identitäts-ID
        self.assertEqual(eff.id, conn.id)
        self.assertEqual(conn.user, "alt")       # Original bleibt unverändert
        self.assertEqual(launcher.build_ssh_args(eff), ["ssh", "-i", "/k/id", "--", "deploy@h"])

    def test_identity_without_password_ignores_connection_password(self):
        cfg, ident, conn = self._cfg()
        ident.use_password = False
        eff, _ = launcher.resolve(cfg, conn)
        self.assertFalse(eff.use_password)

    def test_unknown_or_no_identity_uses_connection_values(self):
        cfg, _, conn = self._cfg()
        conn.identity_id = "gibt-es-nicht"
        eff, key = launcher.resolve(cfg, conn)
        self.assertEqual((eff.user, key), ("alt", conn.id))
        conn.identity_id = ""
        self.assertIs(launcher.resolve(cfg, conn)[0], conn)

    def test_roundtrip_identities(self):
        cfg, ident, conn = self._cfg()
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "c.json"
            storage.save(cfg, p)
            loaded = storage.load(p)
            self.assertEqual(loaded.identities, cfg.identities)
            self.assertEqual(loaded.connections[0].identity_id, ident.id)

    def test_wrapper_uses_identity_password_key(self):
        from unittest import mock
        from sesam import connect_cli, passwords
        cfg, ident, conn = self._cfg()
        asked = []
        with mock.patch.object(connect_cli.storage, "load", return_value=cfg), \
             mock.patch.object(connect_cli.passwords, "get_password", side_effect=lambda k: asked.append(k) or "geheim"), \
             mock.patch.object(connect_cli.askpass, "askpass_path", return_value="/a"), \
             mock.patch.object(connect_cli, "ensure_known_hosts", return_value=True), \
             mock.patch.object(connect_cli, "run_ssh", return_value=(0, "")) as ex:
            connect_cli.run(conn.id)
        self.assertEqual(asked, [ident.id])
        args, env = ex.call_args.args
        self.assertEqual(args[-1], "deploy@h")
        self.assertEqual(env["SSHV_PASSWORD"], "geheim")

    def test_apply_change(self):
        from unittest import mock
        from sesam import passwords
        from sesam.models import Identity
        store = {}
        i = Identity("x")
        with mock.patch.object(passwords, "set_password", lambda k, p: store.__setitem__(k, p)), \
             mock.patch.object(passwords, "delete_password", lambda k: store.pop(k, None)):
            passwords.apply_change(i, None)
            self.assertFalse(i.use_password)
            passwords.apply_change(i, "pw")
            self.assertTrue(i.use_password)
            self.assertEqual(store[i.id], "pw")
            passwords.apply_change(i, "")
            self.assertFalse(i.use_password)
            self.assertEqual(store, {})


class RuntimeTests(unittest.TestCase):
    def test_normal_python_command(self):
        from sesam import runtime
        cmd = runtime.self_command()
        self.assertEqual(cmd[1:], ["-m", "sesam"])
        c = Connection("n", "h")
        self.assertEqual(launcher.wrapper_args(c)[1:], ["-m", "sesam", "--connect", c.id])

    def test_frozen_uses_cli_sibling_when_present(self):
        import os
        from unittest import mock
        from sesam import runtime
        with tempfile.TemporaryDirectory() as d:
            gui = Path(d) / "sesam"
            gui.write_text("")
            with mock.patch.object(runtime.sys, "frozen", True, create=True), \
                 mock.patch.object(runtime.sys, "executable", str(gui)), mock.patch.object(runtime, "IS_WINDOWS", False):
                self.assertEqual(runtime.self_command(), [str(gui)])      # kein Geschwister -> dasselbe Programm
                (Path(d) / "sesam-cli").write_text("")
                self.assertEqual(runtime.self_command(), [str(Path(d) / "sesam-cli")])
                c = Connection("n", "h")
                self.assertEqual(launcher.wrapper_args(c), [str(Path(d) / "sesam-cli"), "--connect", c.id])

    def test_clean_env_restores_library_path_when_frozen(self):
        from unittest import mock
        from sesam import runtime
        env = {"LD_LIBRARY_PATH": "/tmp/_MEI123", "LD_LIBRARY_PATH_ORIG": "/usr/lib/x", "_PYI_APPLICATION_HOME_DIR": "/x", "PATH": "/bin"}
        with mock.patch.object(runtime.sys, "frozen", True, create=True):
            out = runtime.clean_env(env)
        self.assertEqual(out["LD_LIBRARY_PATH"], "/usr/lib/x")
        self.assertNotIn("LD_LIBRARY_PATH_ORIG", out)
        self.assertNotIn("_PYI_APPLICATION_HOME_DIR", out)
        self.assertEqual(out["PYINSTALLER_RESET_ENVIRONMENT"], "1")
        self.assertEqual(out["PATH"], "/bin")
        with mock.patch.object(runtime.sys, "frozen", True, create=True):
            out = runtime.clean_env({"LD_LIBRARY_PATH": "/tmp/_MEI123"})
        self.assertNotIn("LD_LIBRARY_PATH", out)       # nichts Ursprüngliches vorhanden -> entfernen
        self.assertEqual(runtime.clean_env({"LD_LIBRARY_PATH": "/x"}), {"LD_LIBRARY_PATH": "/x"})  # nicht gebündelt: unverändert
