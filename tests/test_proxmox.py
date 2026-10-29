"""Tests für die Proxmox-Anbindung: Gäste lesen, Befehle bauen, per SSH abfragen, Anzeige im Baum."""
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))

from gui_base import GuiTestCase
from sesam import proxmox, storage
from sesam.models import Config, Connection

try:
    import paramiko
except ImportError:
    paramiko = None


def listing(*guests, node="pve"):
    items = [{"id": f"{k}/{i}", "type": k, "vmid": i, "name": n, "status": s, "node": nd, **({"template": 1} if t else {})}
             for (k, i, n, s, nd, t) in guests]
    return (node + "\n" + json.dumps(items)).encode()


GUESTS = (("lxc", 101, "web", "running", "pve", False), ("qemu", 100, "win", "stopped", "pve", False),
          ("qemu", 102, "db", "running", "pve2", False), ("qemu", 900, "vorlage", "stopped", "pve", True))


class ParseTests(unittest.TestCase):
    def test_guests_sorted_by_id_without_templates(self):
        found = proxmox.parse_resources(listing(*GUESTS).decode())
        self.assertEqual([(g.vmid, g.name, g.kind) for g in found], [(100, "win", "qemu"), (101, "web", "lxc"), (102, "db", "qemu")])
        self.assertEqual([g.local for g in found], [True, True, False])
        self.assertEqual([g.running for g in found], [False, True, True])

    def test_junk_is_rejected_or_skipped(self):
        self.assertEqual(proxmox.parse_resources(""), [])
        with self.assertRaises(proxmox.ProxmoxError):
            proxmox.parse_resources("pve\nkein json")
        with self.assertRaises(proxmox.ProxmoxError):
            proxmox.parse_resources('pve\n{"a": 1}')
        self.assertEqual(proxmox.parse_resources('pve\n[1, {"type": "storage"}, {"type": "lxc", "vmid": "x"}]'), [])

    def test_describe_error(self):
        self.assertIn("kein Proxmox", proxmox.describe_error(127, "bash: pvesh: command not found"))
        self.assertIn("root", proxmox.describe_error(1, "permission denied"))
        self.assertEqual(proxmox.describe_error(2, "boom\nzweite"), "Proxmox meldet: boom")


class CommandTests(unittest.TestCase):
    def guest(self, kind="lxc", vmid=101, local=True, node="pve"):
        return proxmox.Guest(vmid, "g", kind, "running", node, local)

    def test_lxc_shell(self):
        self.assertEqual(proxmox.shell_command(self.guest()), "pct enter 101")

    def test_lxc_on_other_node_goes_through_ssh(self):
        self.assertEqual(proxmox.shell_command(self.guest(local=False, node="pve2")), "ssh -t pve2 'pct enter 101'")

    def test_vm_has_no_host_shell(self):
        with self.assertRaises(proxmox.ProxmoxError):
            proxmox.shell_command(self.guest("qemu"))

    def test_bad_node_name_is_refused(self):
        with self.assertRaises(proxmox.ProxmoxError):
            proxmox.power_command(self.guest(node="x; rm -rf /"), "start")

    def test_power_commands_use_the_api(self):
        self.assertEqual(proxmox.power_command(self.guest("qemu", 100), "reboot"),
                         "pvesh create /nodes/pve/qemu/100/status/reboot")
        with self.assertRaises(proxmox.ProxmoxError):
            proxmox.power_command(self.guest(), "format")

    def test_pick_address_skips_loopback_and_link_local(self):
        data = {"result": [
            {"name": "lo", "ip-addresses": [{"ip-address": "127.0.0.1", "ip-address-type": "ipv4"}]},
            {"name": "eth0", "ip-addresses": [{"ip-address": "fe80::1", "ip-address-type": "ipv6"},
                                              {"ip-address": "2001:db8::5", "ip-address-type": "ipv6"},
                                              {"ip-address": "192.168.1.20", "ip-address-type": "ipv4"}]}]}
        self.assertEqual(proxmox.pick_address(data), "192.168.1.20")
        self.assertEqual(proxmox.pick_address({"result": []}), "")
        self.assertEqual(proxmox.pick_address("x"), "")

    def test_vm_command(self):
        self.assertEqual(proxmox.vm_command("10.0.0.5"), "ssh -t root@10.0.0.5")
        self.assertIn("admin@10.0.0.5", proxmox.vm_command("10.0.0.5", " admin "))

    def test_guest_target_titles_the_connection(self):
        titled, cmd = proxmox.guest_target(Connection(name="PVE", host="h"), self.guest(), "x")
        self.assertEqual((titled.name, cmd), ("PVE · g", "x"))


class FakePve:
    def __init__(self):
        self.reply = (listing(*GUESTS), b"", 0)
        self.agent = (json.dumps({"result": [{"name": "eth0", "ip-addresses": [{"ip-address": "10.0.0.7", "ip-address-type": "ipv4"}]}]}).encode(), b"", 0)

    def __call__(self, command):
        if "agent/network-get-interfaces" in command:
            return self.agent
        if command.startswith("pvesh create"):
            return (b"UPID:pve:1", b"", 0)
        return self.reply


@unittest.skipIf(paramiko is None, "paramiko nicht verfügbar")
class SshTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.host_key = paramiko.RSAKey.generate(2048)

    def setUp(self):
        from sftp_server import SftpTestServer

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.pve = FakePve()
        self.server = SftpTestServer(str(self.base), host_key=self.host_key, exec_handler=self.pve)
        self.addCleanup(self.server.close)
        self.cfg = Config()
        self.conn = Connection(name="PVE", host="127.0.0.1", port=self.server.port, user="tester", proxmox=True)
        self.kw = dict(password="geheim", known_hosts=self.base / "known_hosts")

    def test_list_guests(self):
        found = proxmox.list_guests(self.cfg, self.conn, **self.kw)
        self.assertEqual([g.vmid for g in found], [100, 101, 102])
        self.assertEqual(self.server.commands, [proxmox.LIST_COMMAND])

    def test_not_a_proxmox_host(self):
        self.pve.reply = (b"", b"bash: pvesh: command not found\n", 127)
        with self.assertRaises(proxmox.ProxmoxError) as ctx:
            proxmox.list_guests(self.cfg, self.conn, **self.kw)
        self.assertIn("kein Proxmox", str(ctx.exception))

    def test_vm_address_from_guest_agent(self):
        guest = proxmox.Guest(102, "db", "qemu", "running", "pve2", False)
        self.assertEqual(proxmox.vm_address(self.cfg, self.conn, guest, **self.kw), "10.0.0.7")
        self.assertIn("/nodes/pve2/qemu/102/agent/network-get-interfaces", self.server.commands[0])

    def test_vm_address_without_agent_is_a_readable_error(self):
        self.pve.agent = (b"", b"QEMU guest agent is not running\n", 255)
        guest = proxmox.Guest(100, "win", "qemu", "running", "pve", True)
        with self.assertRaises(proxmox.ProxmoxError) as ctx:
            proxmox.vm_address(self.cfg, self.conn, guest, **self.kw)
        self.assertIn("Gast-Agent", str(ctx.exception))

    def test_power(self):
        guest = proxmox.Guest(100, "win", "qemu", "stopped", "pve", True)
        proxmox.power(self.cfg, self.conn, guest, "start", **self.kw)
        self.assertEqual(self.server.commands, ["pvesh create /nodes/pve/qemu/100/status/start"])


@unittest.skipIf(paramiko is None, "paramiko nicht verfügbar")
class TreeTests(GuiTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if paramiko is not None:
            cls.host_key = paramiko.RSAKey.generate(2048)

    def setUp(self):
        super().setUp()
        from sesam import passwords, sftp
        from sftp_server import SftpTestServer

        base = Path(self.tmp.name)
        (base / "remote").mkdir()
        self.pve = FakePve()
        self.server = SftpTestServer(str(base / "remote"), host_key=self.host_key, exec_handler=self.pve)
        self.addCleanup(self.server.close)
        self.conn = Connection(name="PVE", host="127.0.0.1", port=self.server.port, user="tester", use_password=True,
                               proxmox=True, proxmox_vm_user="admin")
        storage.save(Config(connections=[self.conn]), self.path)
        for target, name, fake in ((passwords, "get_password", lambda _i: "geheim"), (sftp, "default_known_hosts", lambda: base / "known_hosts")):
            patcher = mock.patch.object(target, name, fake)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.w = self._window()
        self.addCleanup(self.w.close)

    def wait(self, condition, timeout=8.0):
        deadline = time.monotonic() + timeout
        while not condition() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.005)
        self.assertTrue(condition(), "Bedingung wurde nicht rechtzeitig erfüllt")

    def host(self):
        return self.w.tree.topLevelItem(0)

    def texts(self):
        return [self.host().child(i).text(0) for i in range(self.host().childCount())]

    def expand(self):
        self.host().setExpanded(True)
        self.wait(lambda: self.texts() == ["win (100)", "web (101)", "db (102)"])

    def select(self, row):
        self.w.tree.setCurrentItem(self.host().child(row))

    def test_guests_are_shown_with_kind_and_state(self):
        self.expand()
        self.assertEqual([self.host().child(i).text(1) for i in range(3)],
                         ["VM · gestoppt", "Container · läuft", "VM · läuft"])

    def test_lxc_opens_pct_enter_through_the_host(self):
        self.expand()
        self.select(1)
        with mock.patch("sesam.launcher.launch") as launch:
            self.w.connect_selected()
        titled = launch.call_args.args[1]
        self.assertEqual(titled.name, "PVE · web")
        self.assertEqual(launch.call_args.kwargs["remote_command"], "pct enter 101")

    def test_vm_jumps_from_the_host_to_the_address_from_the_agent(self):
        self.expand()
        self.select(2)
        with mock.patch("sesam.launcher.launch") as launch:
            self.w.connect_selected()
            self.wait(lambda: launch.called)
        self.assertEqual(launch.call_args.kwargs["remote_command"],
                         "ssh -t admin@10.0.0.7")

    def test_stopped_vm_is_not_connected(self):
        self.expand()
        self.select(0)
        with mock.patch("sesam.launcher.launch") as launch, mock.patch("sesam.gui.main_window.QMessageBox.information") as info:
            self.w.connect_selected()
        self.assertFalse(launch.called)
        self.assertTrue(info.called)

    def test_power_asks_for_hard_actions_and_reloads(self):
        self.expand()
        self.select(1)
        box = "sesam.gui.main_window.QMessageBox.question"
        from PySide6.QtWidgets import QMessageBox
        with mock.patch(box, return_value=QMessageBox.StandardButton.No) as q:
            self.w.power_guest("stop")
        self.assertTrue(q.called)
        self.assertFalse(any(c.startswith("pvesh create") for c in self.server.commands))
        with mock.patch(box) as q:
            self.w.power_guest("shutdown")  # sanft: keine Rückfrage
        self.assertFalse(q.called)
        self.wait(lambda: "pvesh create /nodes/pve/lxc/101/status/shutdown" in self.server.commands)

    def test_dialog_makes_docker_and_proxmox_exclusive(self):
        from sesam.gui.connection_dialog import ConnectionDialog
        dlg = ConnectionDialog(Config(connections=[self.conn]), Connection(name="x", host="h", docker=True))
        self.assertTrue(dlg.docker.isChecked())
        dlg.proxmox.setChecked(True)
        self.assertFalse(dlg.docker.isChecked())
        dlg.name.setText("x")
        dlg.host.setText("h")
        dlg._accept()
        self.assertTrue(dlg.result_connection().proxmox)
        self.assertFalse(dlg.result_connection().docker)


if __name__ == "__main__":
    unittest.main()
