"""Tests für die Cisco-Anbindung: Portliste lesen, Konfigurationsbefehle, SSH-Shell gegen einen simulierten Switch, Anzeige im Baum."""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))

from gui_base import GuiTestCase
from sesam import cisco, storage
from sesam.models import Config, Connection

try:
    import paramiko
except ImportError:
    paramiko = None

FMT = "{:<10}{:<19}{:<13}{:<11}{:<8}{:<6}{}"
HEADER = FMT.format("Port", "Name", "Status", "Vlan", "Duplex", "Speed", "Type")


def table(ports):
    rows = [HEADER] + [FMT.format(n, p["name"], p["status"], p["vlan"], "a-full", "a-1000", "10/100/1000BaseTX") for n, p in ports.items()]
    return "\n".join(rows)


def fresh_ports():
    return {"Gi1/0/1": dict(name="Uplink", status="connected", vlan="trunk"),
            "Gi1/0/2": dict(name="", status="notconnect", vlan="10"),
            "Gi1/0/3": dict(name="Drucker EG", status="disabled", vlan="20")}


class FakeSwitch:
    """Spricht wie eine IOS-Shell: Echo, Ausgabe, Prompt; merkt sich Befehle und Zustand."""

    def __init__(self, privileged=True, nxos=False):
        self.privileged, self.nxos = privileged, nxos
        self.lines: list[str] = []
        self.ports = fresh_ports()
        self.mode, self.iface, self.saved = "exec", None, False

    def prompt(self):
        if not self.privileged:
            return "sw1>"
        return {"exec": "sw1#", "config": "sw1(config)#", "if": "sw1(config-if)#"}[self.mode]

    def shell(self, ch):
        ch.sendall(("\r\nWelcome\r\n" + self.prompt()).encode())
        buf = ""
        while True:
            data = ch.recv(1024)
            if not data:
                return
            buf += data.decode()
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                out = self.handle(line.strip())
                ch.sendall((line.strip() + "\r\n" + (out + "\r\n" if out else "") + self.prompt()).encode())

    def handle(self, line):
        self.lines.append(line)
        bad = "% Invalid input detected at '^' marker."
        if line == "terminal length 0":
            return ""
        if line == "show interfaces status":
            return table(self.ports)
        if line == "configure terminal" and self.mode == "exec":
            self.mode = "config"
            return ""
        if line == "end":
            self.mode = "exec"
            return ""
        if line.startswith("interface ") and self.mode in ("config", "if"):
            name = line.split(" ", 1)[1]
            if name not in self.ports:
                return bad
            self.mode, self.iface = "if", name
            return ""
        if self.mode == "if" and line in ("shutdown", "no shutdown"):
            self.ports[self.iface]["status"] = "disabled" if line == "shutdown" else "notconnect"
            return ""
        if self.mode == "if" and line.startswith("description "):
            self.ports[self.iface]["name"] = line[len("description "):]
            return ""
        if self.mode == "if" and line == "no description":
            self.ports[self.iface]["name"] = ""
            return ""
        if line == "write memory" and not self.nxos:
            self.saved = True
            return "Building configuration...\r\n[OK]"
        if line == "copy running-config startup-config" and self.nxos:
            self.saved = True
            return "[########################################] 100%"
        return bad


class ParseTests(unittest.TestCase):
    def test_ios_table(self):
        found = cisco.parse_status(table(fresh_ports()))
        self.assertEqual([p.name for p in found], ["Gi1/0/1", "Gi1/0/2", "Gi1/0/3"])
        self.assertEqual((found[0].description, found[0].vlan, found[0].up), ("Uplink", "trunk", True))
        self.assertEqual((found[1].description, found[1].up, found[1].disabled), ("", False, False))
        self.assertTrue(found[2].disabled)
        self.assertEqual(found[0].type, "10/100/1000BaseTX")

    def test_prompt_noise_and_blank_lines_are_ignored(self):
        text = "\n" + table(fresh_ports()) + "\n\n"
        self.assertEqual(len(cisco.parse_status(text)), 3)

    def test_nxos_style_columns(self):
        head = "Port          Name               Status    Vlan      Duplex  Speed   Type"
        row = "Eth1/1        Server1            connected 100       full    10G     10Gbase-SR"
        found = cisco.parse_status(head + "\n" + row)
        self.assertEqual((found[0].name, found[0].description, found[0].status, found[0].vlan, found[0].speed),
                         ("Eth1/1", "Server1", "connected", "100", "10G"))

    def test_without_table_is_an_error(self):
        with self.assertRaises(cisco.CiscoError):
            cisco.parse_status("% Invalid input detected")


class CommandTests(unittest.TestCase):
    def test_config_commands(self):
        self.assertEqual(cisco.config_commands("Gi1/0/1", shutdown=True),
                         ["configure terminal", "interface Gi1/0/1", "shutdown", "end"])
        self.assertEqual(cisco.config_commands("Gi1/0/1", shutdown=False)[2], "no shutdown")
        self.assertEqual(cisco.config_commands("Gi1/0/1", description="  Büro   2 ")[2], "description Büro 2")
        self.assertEqual(cisco.config_commands("Gi1/0/1", description="")[2], "no description")

    def test_dangerous_input_is_refused(self):
        for name in ("Gi1/0/1; reload", "Gi1/0/1\nreload", "", "1/0/1", "Gi1/0/1 shutdown"):
            with self.subTest(name=name), self.assertRaises(cisco.CiscoError):
                cisco.config_commands(name, shutdown=True)
        for text in ("was?", "x" * 201):
            with self.subTest(text=text[:5]), self.assertRaises(cisco.CiscoError):
                cisco.check_description(text)

    def test_newlines_in_descriptions_become_spaces(self):
        self.assertEqual(cisco.check_description("a\nreload\nb"), "a reload b")  # bleibt eine Zeile, also ein Befehl

    def test_find_error(self):
        self.assertEqual(cisco.find_error("ok\n% Invalid input detected at '^' marker.\n"), "% Invalid input detected at '^' marker.")
        self.assertEqual(cisco.find_error("% Incomplete command."), "% Incomplete command.")
        self.assertEqual(cisco.find_error("Gi1/0/1 connected"), "")


@unittest.skipIf(paramiko is None, "paramiko nicht verfügbar")
class SshTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.host_key = paramiko.RSAKey.generate(2048)

    def start(self, **kw):
        from sftp_server import SftpTestServer

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.switch = FakeSwitch(**kw)
        self.server = SftpTestServer(str(self.base), host_key=self.host_key, shell_handler=self.switch.shell)
        self.addCleanup(self.server.close)
        self.cfg = Config()
        self.conn = Connection(name="SW", host="127.0.0.1", port=self.server.port, user="tester", cisco=True)
        self.kw = dict(password="geheim", known_hosts=self.base / "known_hosts")

    def test_list_ports(self):
        self.start()
        found = cisco.list_ports(self.cfg, self.conn, **self.kw)
        self.assertEqual([p.name for p in found], ["Gi1/0/1", "Gi1/0/2", "Gi1/0/3"])
        self.assertEqual(self.switch.lines, ["terminal length 0", "show interfaces status"])

    def test_user_mode_is_explained(self):
        self.start(privileged=False)
        with self.assertRaises(cisco.CiscoError) as ctx:
            cisco.list_ports(self.cfg, self.conn, **self.kw)
        self.assertIn("Privilegstufe 15", str(ctx.exception))

    def test_shutdown_and_enable(self):
        self.start()
        cisco.set_shutdown(self.cfg, self.conn, "Gi1/0/2", True, **self.kw)
        self.assertEqual(self.switch.ports["Gi1/0/2"]["status"], "disabled")
        self.assertEqual(self.switch.lines[1:], ["configure terminal", "interface Gi1/0/2", "shutdown", "end"])
        cisco.set_shutdown(self.cfg, self.conn, "Gi1/0/3", False, **self.kw)
        self.assertEqual(self.switch.ports["Gi1/0/3"]["status"], "notconnect")

    def test_description(self):
        self.start()
        cisco.set_description(self.cfg, self.conn, "Gi1/0/2", "Büro 3", **self.kw)
        self.assertEqual(self.switch.ports["Gi1/0/2"]["name"], "Büro 3")
        cisco.set_description(self.cfg, self.conn, "Gi1/0/2", "", **self.kw)
        self.assertEqual(self.switch.ports["Gi1/0/2"]["name"], "")

    def test_device_error_is_reported_and_config_mode_left(self):
        self.start()
        with self.assertRaises(cisco.CiscoError) as ctx:
            cisco.set_shutdown(self.cfg, self.conn, "Gi9/9/9", True, **self.kw)
        self.assertIn("Invalid input", str(ctx.exception))
        deadline = time.monotonic() + 3
        while self.switch.mode != "exec" and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertEqual(self.switch.mode, "exec")

    def test_save_ios_and_nxos(self):
        self.start()
        cisco.save_config(self.cfg, self.conn, **self.kw)
        self.assertTrue(self.switch.saved)
        self.assertEqual(self.switch.lines[-1], "write memory")

    def test_save_falls_back_to_copy_on_nxos(self):
        self.start(nxos=True)
        cisco.save_config(self.cfg, self.conn, **self.kw)
        self.assertTrue(self.switch.saved)
        self.assertEqual(self.switch.lines[-2:], ["write memory", "copy running-config startup-config"])


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
        self.switch = FakeSwitch()
        self.server = SftpTestServer(str(base / "remote"), host_key=self.host_key, shell_handler=self.switch.shell)
        self.addCleanup(self.server.close)
        self.conn = Connection(name="SW", host="127.0.0.1", port=self.server.port, user="tester", use_password=True, cisco=True)
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

    def texts(self, col=0):
        return [self.host().child(i).text(col) for i in range(self.host().childCount())]

    def row(self, i):
        """Text der Spalte 1 in Zeile ``i`` oder "" (während des Neuladens gibt es die Zeile kurz nicht)."""
        t = self.texts(1)
        return t[i] if i < len(t) else ""

    def expand(self):
        self.host().setExpanded(True)
        self.wait(lambda: self.texts() == ["Gi1/0/1", "Gi1/0/2", "Gi1/0/3"])

    def select(self, row):
        self.w.tree.setCurrentItem(self.host().child(row))

    def test_ports_are_shown_with_state_description_and_vlan(self):
        self.expand()
        self.assertEqual(self.texts(1), ["verbunden · Uplink · VLAN trunk · a-1000", "notconnect · VLAN 10 · a-1000", "aus · Drucker EG · VLAN 20 · a-1000"])

    def test_shutdown_asks_and_reloads(self):
        from PySide6.QtWidgets import QMessageBox
        self.expand()
        self.select(1)
        box = "sesam.gui.main_window.QMessageBox.question"
        with mock.patch(box, return_value=QMessageBox.StandardButton.No):
            self.w.port_shutdown(True)
        self.assertEqual(self.switch.ports["Gi1/0/2"]["status"], "notconnect")
        with mock.patch(box, return_value=QMessageBox.StandardButton.Yes):
            self.w.port_shutdown(True)
        self.wait(lambda: self.row(1).startswith("aus"))
        self.assertEqual(self.switch.ports["Gi1/0/2"]["status"], "disabled")

    def test_enable_needs_no_confirmation(self):
        self.expand()
        self.select(2)
        with mock.patch("sesam.gui.main_window.QMessageBox.question") as q:
            self.w.port_shutdown(False)
        self.assertFalse(q.called)
        self.wait(lambda: self.row(2).startswith("notconnect"))

    def test_description_dialog_sets_text(self):
        self.expand()
        self.select(1)
        with mock.patch("sesam.gui.main_window.QInputDialog.getText", return_value=("Büro 5", True)):
            self.w.port_description()
        self.wait(lambda: "Büro 5" in self.row(1))

    def test_description_with_question_mark_is_rejected(self):
        self.expand()
        self.select(1)
        with mock.patch("sesam.gui.main_window.QInputDialog.getText", return_value=("was?", True)), \
                mock.patch("sesam.gui.main_window.QMessageBox.warning") as warn:
            self.w.port_description()
        self.assertTrue(warn.called)
        self.assertNotIn("description was?", self.switch.lines)

    def test_double_click_on_a_port_opens_the_device(self):
        self.expand()
        self.select(0)
        with mock.patch("sesam.launcher.launch") as launch:
            self.w.connect_selected()
        self.assertEqual(launch.call_args.args[1].id, self.conn.id)
        self.assertNotIn("remote_command", launch.call_args.kwargs)

    def test_save_device(self):
        self.w.save_device(self.conn)
        self.wait(lambda: self.switch.saved)

    def test_dialog_options_are_exclusive(self):
        from sesam.gui.connection_dialog import ConnectionDialog
        dlg = ConnectionDialog(Config(connections=[self.conn]), Connection(name="x", host="h", docker=True))
        dlg.cisco.setChecked(True)
        self.assertFalse(dlg.docker.isChecked())
        dlg.proxmox.setChecked(True)
        self.assertFalse(dlg.cisco.isChecked())
        dlg.cisco.setChecked(True)
        dlg.name.setText("x")
        dlg.host.setText("h")
        dlg._accept()
        c = dlg.result_connection()
        self.assertEqual((c.cisco, c.proxmox, c.docker), (True, False, False))


if __name__ == "__main__":
    unittest.main()
