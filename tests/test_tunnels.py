"""Port-Weiterleitungen (SSH-Tunnel): Modell, ssh-Aufruf, Prozessverwaltung und Oberfläche."""
import json
import os
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))

from gui_base import GuiTestCase

from sesam import launcher, storage, syncing, tunnels
from sesam.models import Config, Connection, Forward


def local(**kw):
    return Forward(**{"kind": "local", "listen_port": 8080, "dest_host": "db", "dest_port": 5432, **kw})


class ModelTests(unittest.TestCase):
    def test_roundtrip_through_config_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "c.json"
            conn = Connection("a", "h", forwards=[local(name="DB"), Forward(kind="dynamic", listen_port=1080)])
            storage.save(Config(connections=[conn]), p)
            loaded = storage.load(p).connections[0]
            self.assertEqual(loaded.forwards, conn.forwards)
            self.assertEqual(loaded.forwards[0].dest_host, "db")

    def test_bad_values_never_crash(self):
        for raw in (None, "x", 5, [None, 3, "a"], [{"kind": "evil", "listen_port": 99999, "dest_port": "x"}]):
            c = Connection.from_dict({"name": "a", "host": "h", "forwards": raw})
            self.assertIsInstance(c.forwards, list)
            for f in c.forwards:
                self.assertIn(f.kind, ("local", "remote", "dynamic"))
                self.assertTrue(0 <= f.listen_port <= 65535 and 0 <= f.dest_port <= 65535)

    def test_old_config_without_forwards(self):
        self.assertEqual(Connection.from_dict({"name": "a", "host": "h"}).forwards, [])

    def test_forwards_are_part_of_the_synced_entry(self):
        conn = Connection("a", "h", forwards=[local()])
        item = syncing.item_from_obj("c", conn, None)
        self.assertEqual(item.data["forwards"][0]["dest_port"], 5432)
        again = Connection.from_dict(dict(item.data, id=conn.id))
        self.assertEqual(again.forwards, conn.forwards)


class SpecTests(unittest.TestCase):
    def test_kinds(self):
        self.assertEqual(tunnels.forward_spec(local()), ["-L", "127.0.0.1:8080:db:5432"])
        self.assertEqual(tunnels.forward_spec(local(kind="remote", listen_port=9000, dest_host="localhost", dest_port=3000)),
                         ["-R", "localhost:9000:localhost:3000"])
        self.assertEqual(tunnels.forward_spec(Forward(kind="dynamic", listen_port=1080)), ["-D", "127.0.0.1:1080"])

    def test_explicit_bind_address_and_ipv6(self):
        self.assertEqual(tunnels.forward_spec(local(listen_host="0.0.0.0")), ["-L", "0.0.0.0:8080:db:5432"])
        self.assertEqual(tunnels.forward_spec(local(listen_host="*")), ["-L", "*:8080:db:5432"])
        self.assertEqual(tunnels.forward_spec(local(listen_host="::1", dest_host="fe80::1")), ["-L", "[::1]:8080:[fe80::1]:5432"])

    def test_invalid_values_are_rejected(self):
        for bad in (local(listen_port=0), local(listen_port=70000), local(dest_port=0), local(dest_host=""),
                    local(dest_host="-oProxyCommand=x"), local(dest_host="a b"), local(listen_host="a:b"),
                    local(listen_host="-x"), local(kind="evil"), local(dest_host="a,b"), local(dest_host="a/b")):
            with self.subTest(bad=bad), self.assertRaises(tunnels.TunnelError):
                tunnels.forward_spec(bad)

    def test_loopback(self):
        for host in ("", "localhost", "127.0.0.1", "::1", "[::1]"):
            self.assertTrue(tunnels.is_loopback(host), host)
        for host in ("0.0.0.0", "*", "192.168.1.5", "example.org"):
            self.assertFalse(tunnels.is_loopback(host), host)

    def test_labels(self):
        self.assertEqual(tunnels.describe(local()), "127.0.0.1:8080 → db:5432")
        self.assertEqual(tunnels.label(local(name="DB")), "DB – 127.0.0.1:8080 → db:5432")
        self.assertIn("1080", tunnels.describe(Forward(kind="dynamic", listen_port=1080)))


class ArgsTests(unittest.TestCase):
    def test_tunnel_never_runs_a_command_or_sudo(self):
        conn = Connection("a", "srv", user="root", port=2222, jump_host="jump", remote_command="top", sudo_mode="login",
                          extra_args="-o StrictHostKeyChecking=yes")
        args = tunnels.build_args(conn, local())
        self.assertEqual(args[0], "ssh")
        self.assertNotIn("-t", args)
        self.assertNotIn("top", args)
        self.assertIn("-N", args)
        self.assertEqual(args[-2:], ["--", "root@srv"])
        self.assertEqual(args[args.index("-p") + 1], "2222")
        self.assertEqual(args[args.index("-J") + 1], "jump")
        self.assertEqual(args[args.index("-L") + 1], "127.0.0.1:8080:db:5432")
        self.assertIn("ExitOnForwardFailure=yes", args)

    def test_forward_option_comes_before_the_target(self):
        args = tunnels.build_args(Connection("a", "srv"), local())
        self.assertLess(args.index("-L"), args.index("--"))

    def test_batch_mode_only_without_password(self):
        conn = Connection("a", "srv")
        self.assertIn("BatchMode=yes", tunnels.build_args(conn, local(), with_password=False))
        with_pw = tunnels.build_args(conn, local(), with_password=True)
        self.assertNotIn("BatchMode=yes", with_pw)
        self.assertIn("NumberOfPasswordPrompts=1", with_pw)

    def test_password_goes_through_askpass_environment(self):
        args, env = tunnels.prepare(Connection("a", "srv", use_password=True), local(), "geheim", "/x/askpass.sh")
        self.assertEqual(env["SSH_ASKPASS"], "/x/askpass.sh")
        self.assertEqual(env["SSHV_PASSWORD"], "geheim")
        self.assertNotIn("geheim", " ".join(args))
        _, env = tunnels.prepare(Connection("a", "srv"), local(), None)
        self.assertNotIn("SSH_ASKPASS", env)

    def test_hostile_target_is_refused(self):
        with self.assertRaises(launcher.LaunchError):
            tunnels.build_args(Connection("a", "-oProxyCommand=x"), local())


def fake_ssh(tmp: str, body: str) -> list[str]:
    path = Path(tmp) / "fake_ssh.py"
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return [sys.executable, str(path)]


def wait_for(cond, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


class ProcessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.mgr = tunnels.Manager()
        self.addCleanup(self.mgr.stop_all)

    def _tunnel(self, body, forward=None):
        args = fake_ssh(self.tmp.name, body)
        t = tunnels.Tunnel("c1", forward or local(), args, dict(os.environ))
        self.mgr._tunnels[t.key] = t
        t.start()
        return t

    def test_becomes_running_stops_cleanly(self):
        t = self._tunnel("""
            import sys, time
            sys.stderr.write("debug1: Entering interactive session.\\n"); sys.stderr.flush()
            time.sleep(60)
        """)
        self.assertTrue(wait_for(lambda: t.state == tunnels.RUNNING))
        self.assertEqual([x.key for x in self.mgr.active()], [t.key])
        self.mgr.stop(*t.key)
        self.assertEqual(t.state, tunnels.STOPPED)
        self.assertEqual(self.mgr.active(), [])

    def test_failure_reports_readable_error_once(self):
        t = self._tunnel("""
            import sys
            sys.stderr.write("debug1: connecting\\nWarning: x\\nuser@srv: Permission denied (publickey).\\n")
            sys.exit(255)
        """)
        self.assertTrue(wait_for(lambda: t.state == tunnels.FAILED))
        self.assertIn("Permission denied", t.error)
        self.assertNotIn("debug1", t.error)
        self.assertEqual(self.mgr.newly_failed(), [t])
        self.assertEqual(self.mgr.newly_failed(), [])

    def test_failure_without_output_names_exit_code(self):
        t = self._tunnel("import sys; sys.exit(7)")
        self.assertTrue(wait_for(lambda: t.state == tunnels.FAILED))
        self.assertIn("7", t.error)

    def test_start_is_not_doubled(self):
        conn = Connection("a", "srv", id="c1")
        f = local()
        script = fake_ssh(self.tmp.name, "import time; time.sleep(60)")
        with mock.patch.object(tunnels, "prepare", return_value=(script, dict(os.environ))):
            first = self.mgr.start(conn, f, None)
            again = self.mgr.start(conn, f, None)
        self.assertIs(first, again)

    def test_missing_ssh_gives_clear_error(self):
        t = tunnels.Tunnel("c1", local(), ["definitely-not-ssh-xyz"], {})
        with self.assertRaises(tunnels.TunnelError):
            t.start()

    def test_forget_missing_stops_deleted_or_changed(self):
        f = local()
        conn = Connection("a", "srv", id="c1", forwards=[f])
        cfg = Config(connections=[conn])
        t = self._tunnel("import time; time.sleep(60)", f)
        self.mgr.forget_missing(cfg)
        self.assertEqual(self.mgr.active(), [t])
        conn.forwards = [Forward(**{**f.__dict__, "dest_port": 1})]  # geändert: der laufende Tunnel passt nicht mehr
        self.mgr.forget_missing(cfg)
        self.assertEqual(self.mgr.active(), [])
        self.assertEqual(t.state, tunnels.STOPPED)


class ErrorTextTests(unittest.TestCase):
    def test_readable_error_filters_debug_noise(self):
        log = ["OpenSSH_9.6p1, OpenSSL 3", "debug1: Reading configuration", "bind [127.0.0.1]:80: Permission denied",
               "Could not request local forwarding.", "Authenticated to x"]
        text = tunnels.readable_error(log, 255)
        self.assertIn("Permission denied", text)
        self.assertNotIn("debug1", text)
        self.assertNotIn("OpenSSH_", text)


class HostKeyTests(unittest.TestCase):
    def test_strict_checking_skips_pre_check(self):
        conn = Connection("a", "srv", extra_args="-o StrictHostKeyChecking=yes")
        with mock.patch.object(tunnels.subprocess, "run", side_effect=AssertionError("nicht erwartet")):
            self.assertEqual(tunnels.pending_host_keys(conn), [])

    def test_unknown_host_is_returned_with_fingerprints(self):
        conn = Connection("a", "srv.example")
        scan = mock.Mock(stdout="srv.example ssh-ed25519 AAAA\n# kommentar\n", returncode=0)
        shown = mock.Mock(stdout="256 SHA256:abc srv.example (ED25519)\n", returncode=0)
        with mock.patch.object(tunnels.shutil, "which", return_value="/usr/bin/x"), \
                mock.patch.object(tunnels.connect_cli, "_is_known", return_value=False), \
                mock.patch.object(tunnels.subprocess, "run", side_effect=[scan, shown]):
            found = tunnels.pending_host_keys(conn)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].name, "srv.example")
        self.assertEqual(found[0].lines, ["srv.example ssh-ed25519 AAAA"])
        self.assertIn("SHA256:abc", found[0].shown)

    def test_known_host_needs_nothing_and_unreachable_host_is_an_error(self):
        conn = Connection("a", "srv.example")
        with mock.patch.object(tunnels.shutil, "which", return_value="/usr/bin/x"), \
                mock.patch.object(tunnels.connect_cli, "_is_known", return_value=True):
            self.assertEqual(tunnels.pending_host_keys(conn), [])
        empty = mock.Mock(stdout="", returncode=1)
        with mock.patch.object(tunnels.shutil, "which", return_value="/usr/bin/x"), \
                mock.patch.object(tunnels.connect_cli, "_is_known", return_value=False), \
                mock.patch.object(tunnels.subprocess, "run", return_value=empty):
            with self.assertRaises(tunnels.TunnelError):
                tunnels.pending_host_keys(conn)

    def test_trust_appends_to_known_hosts(self):
        with tempfile.TemporaryDirectory() as tmp:
            kh = Path(tmp) / ".ssh" / "known_hosts"
            kh.parent.mkdir()
            kh.write_text("old ssh-ed25519 AAAA", encoding="utf-8")  # ohne Zeilenende
            with mock.patch.object(tunnels.connect_cli, "known_hosts_file", return_value=kh):
                tunnels.trust(tunnels.PendingKey("h", "h", 22, ["h ssh-ed25519 BBBB"], ""))
            self.assertEqual(kh.read_text(encoding="utf-8"), "old ssh-ed25519 AAAA\nh ssh-ed25519 BBBB\n")


class GuiTests(GuiTestCase):
    def setUp(self):
        super().setUp()
        self.fwd = local(name="DB")
        self.conn = Connection("web", "srv.example", id="c1", forwards=[self.fwd])
        storage.save(Config(connections=[self.conn]), self.path)

    def _open(self):
        w = self._window()
        self.addCleanup(w.close)
        return w

    def test_dialog_edits_forwards_and_keeps_originals_until_ok(self):
        from sesam.gui.connection_dialog import ConnectionDialog

        dlg = ConnectionDialog(Config(connections=[self.conn]), self.conn)
        self.addCleanup(dlg.close)
        page = dlg.forwards_page
        self.assertEqual(page.list.count(), 1)
        page.forwards.append(Forward(kind="dynamic", listen_port=1080))
        page._fill()
        self.assertEqual(self.conn.forwards, [self.fwd])  # Arbeitskopie: noch nichts geändert
        dlg.name.setText("web")
        dlg._accept()
        self.assertEqual([f.kind for f in self.conn.forwards], ["local", "dynamic"])
        self.assertEqual(self.conn.forwards[0].id, self.fwd.id)  # bestehende Weiterleitungen behalten ihre ID

    def test_edit_dialog_validates_and_warns_about_open_listen_address(self):
        from PySide6.QtWidgets import QMessageBox

        from sesam.gui.forwards_dialog import ForwardEditDialog

        dlg = ForwardEditDialog(local())
        self.addCleanup(dlg.close)
        dlg.dest_host.setText("-oProxyCommand=x")
        with mock.patch.object(QMessageBox, "warning") as warn:
            dlg._accept()
        warn.assert_called_once()
        self.assertEqual(dlg.result(), 0)
        dlg.dest_host.setText("db")
        dlg.listen_host.setText("0.0.0.0")
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No) as ask:
            dlg._accept()
        ask.assert_called_once()
        self.assertEqual(dlg.result(), 0)  # abgelehnt: Dialog bleibt offen
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            dlg._accept()
        self.assertEqual(dlg.result_forward().listen_host, "0.0.0.0")

    def test_dynamic_hides_destination(self):
        from sesam.gui.forwards_dialog import ForwardEditDialog

        dlg = ForwardEditDialog(Forward(kind="dynamic", listen_port=1080))
        self.addCleanup(dlg.close)
        dlg.show()
        self.assertTrue(dlg.dest_host.isHidden())
        dlg.kind.setCurrentIndex(dlg.kind.findData("local"))
        self.assertFalse(dlg.dest_host.isHidden())

    def test_start_and_stop_from_the_window(self):
        w = self._open()
        started = []

        def fake_start(conn, forward, password):
            started.append((conn.id, forward.id, password))
            t = tunnels.Tunnel(conn.id, forward, [sys.executable, "-c", "import time; time.sleep(60)"], dict(os.environ))
            t.start()
            w.tunnels._tunnels[t.key] = t
            return t

        with mock.patch.object(tunnels, "pending_host_keys", return_value=[]), mock.patch.object(w.tunnels, "start", side_effect=fake_start):
            self.assertTrue(w.start_tunnel(self.conn, self.fwd))
        self.assertEqual(started, [("c1", self.fwd.id, None)])
        self.assertEqual(w._display_name(self.conn), "web  ⇄ 1")
        self.assertIn("⇄ 1", w._find_item("c1").text(0))
        w.toggle_tunnel(self.conn, self.fwd)
        self.assertEqual(w._display_name(self.conn), "web")
        self.assertEqual(w._find_item("c1").text(0), "web")

    def test_unconfirmed_host_key_prevents_start(self):
        from PySide6.QtWidgets import QMessageBox

        w = self._open()
        pending = [tunnels.PendingKey("srv.example", "srv.example", 22, ["x"], "256 SHA256:abc")]
        with mock.patch.object(tunnels, "pending_host_keys", return_value=pending), \
                mock.patch.object(QMessageBox, "exec", return_value=0), \
                mock.patch.object(QMessageBox, "clickedButton", return_value=None), \
                mock.patch.object(tunnels, "trust") as trust, mock.patch.object(w.tunnels, "start") as start:
            self.assertFalse(w.start_tunnel(self.conn, self.fwd))
        trust.assert_not_called()
        start.assert_not_called()

    def test_start_error_is_shown(self):
        from PySide6.QtWidgets import QMessageBox

        w = self._open()
        with mock.patch.object(tunnels, "pending_host_keys", side_effect=tunnels.TunnelError("kein Schlüssel")), \
                mock.patch.object(QMessageBox, "critical") as crit:
            self.assertFalse(w.start_tunnel(self.conn, self.fwd))
        crit.assert_called_once()
        self.assertIn("kein Schlüssel", crit.call_args[0][2])

    def test_failed_tunnel_is_reported_and_closing_stops_all(self):
        w = self._open()
        w.set_toasts(True)
        t = tunnels.Tunnel("c1", self.fwd, [sys.executable, "-c", "import sys; sys.stderr.write('Permission denied\\n'); sys.exit(255)"], dict(os.environ))
        t.start()
        w.tunnels._tunnels[t.key] = t
        self.assertTrue(wait_for(lambda: t.state == tunnels.FAILED))
        w._poll_tunnels()
        self.assertTrue(w.toasts._toast.isVisible())
        self.assertIn("Permission denied", w.toasts._toast.text())
        alive = tunnels.Tunnel("c1", Forward(listen_port=1), [sys.executable, "-c", "import time; time.sleep(60)"], dict(os.environ))
        alive.start()
        w.tunnels._tunnels[alive.key] = alive
        w.close()
        self.assertEqual(alive.state, tunnels.STOPPED)

    def test_context_menu_lists_forwards_only_when_present(self):
        from PySide6.QtWidgets import QMenu

        w = self._open()
        w._select_connection("c1")
        menu = QMenu(w)
        self.addCleanup(menu.deleteLater)
        w._forwards_menu(menu, self.conn)
        sub = menu.actions()[0].menu()
        self.assertEqual(menu.actions()[0].text(), "Weiterleitungen")
        texts = [a.text() for a in sub.actions() if a.text()]
        self.assertEqual(texts[0], tunnels.label(self.fwd))
        self.assertTrue(sub.actions()[0].isCheckable())
        empty = QMenu(w)
        self.addCleanup(empty.deleteLater)
        w._forwards_menu(empty, Connection("x", "y"))
        self.assertEqual(empty.actions(), [])

    def test_duplicate_gives_new_forward_ids(self):
        w = self._open()
        w._select_connection("c1")
        from sesam.gui.connection_dialog import ConnectionDialog

        with mock.patch.object(ConnectionDialog, "exec", return_value=1):
            w.duplicate_selected()
        clone = next(c for c in w.cfg.connections if c.id != "c1")
        self.assertEqual(len(clone.forwards), 1)
        self.assertNotEqual(clone.forwards[0].id, self.fwd.id)
        self.assertEqual(self.conn.forwards[0].id, self.fwd.id)


if __name__ == "__main__":
    unittest.main()
