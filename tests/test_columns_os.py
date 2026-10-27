"""Spalten der Hauptansicht ein-/ausblenden und Betriebssystem des Hosts (bei der ersten Verbindung ermittelt, gespeichert)."""
import json
import time
import unittest
from unittest import mock

from gui_base import GuiTestCase

from sesam import osinfo, sftp, storage
from sesam.models import Config, Connection


class FakeChannel:
    def __init__(self, status):
        self._status = status

    def recv_exit_status(self):
        return self._status


class FakeStream:
    def __init__(self, text, status=0):
        self._text = text
        self.channel = FakeChannel(status)

    def read(self):
        return self._text.encode()


class FakeClient:
    """Antwortet je Befehl mit (Ausgabe, Status)."""

    def __init__(self, answers):
        self.answers, self.commands, self.closed = answers, [], False

    def exec_command(self, command, timeout=None):
        self.commands.append(command)
        index = {osinfo.COMMAND: 0, osinfo.WINDOWS_COMMAND: 1, osinfo.CISCO_COMMAND: 2}[command]
        text, status = self.answers[index] if index < len(self.answers) else ("", 1)
        return None, FakeStream(text, status), FakeStream("")

    def close(self):
        self.closed = True


def open_menu(call):
    """Ruft ``call`` auf, das ein Menü blockierend öffnet; liefert dessen Einträge (das Menü wird sofort wieder geschlossen)."""
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    found = []

    def grab():
        menu = QApplication.activePopupWidget()
        if menu is None:
            QTimer.singleShot(10, grab)
            return
        found.extend(menu.actions())
        menu.close()

    QTimer.singleShot(10, grab)
    call()
    return found


class ParseTests(unittest.TestCase):
    def test_parse(self):
        cases = {
            "Debian GNU/Linux 12 (bookworm)\n": "Debian GNU/Linux 12 (bookworm)",
            "\n\n  Ubuntu 24.04.1 LTS  \n": "Ubuntu 24.04.1 LTS",
            "macOS 14.5\n": "macOS 14.5",
            "Linux 6.1.0-18-amd64\n": "Linux 6.1.0-18-amd64",
            "\nMicrosoft Windows [Version 10.0.19045.4291]\n": "Windows 10.0.19045.4291",
            "": "", "   \n": "",
            "% Invalid input detected at '^' marker.": "",
            "sh: 1: sw_vers: not found": "",
            "Ubuntu\x1b[31m 24\x00": "Ubuntu [31m 24",
        }
        for out, expected in cases.items():
            self.assertEqual(osinfo.parse(out), expected, out)
        self.assertEqual(len(osinfo.parse("x" * 500)), osinfo.MAX_LENGTH)

    def test_command_is_shell_independent(self):
        self.assertTrue(osinfo.COMMAND.startswith("sh -c '"))
        self.assertIn("/etc/os-release", osinfo.COMMAND)


class DetectTests(unittest.TestCase):
    def _detect(self, answers, **kwargs):
        client = FakeClient(answers)
        with mock.patch.object(sftp, "open_client", return_value=(client, None)) as opened:
            found = osinfo.detect(Config(), Connection("x", "h"), **kwargs)
        return found, client, opened

    def test_unix(self):
        found, client, opened = self._detect([("Debian GNU/Linux 12 (bookworm)\n", 0), ("", 1)])
        self.assertEqual((found, client.commands, client.closed), ("Debian GNU/Linux 12 (bookworm)", [osinfo.COMMAND], True))

    def test_windows_fallback(self):
        found, client, _ = self._detect([("", 1), ("\nMicrosoft Windows [Version 10.0.1]\n", 0)])
        self.assertEqual((found, client.commands), ("Windows 10.0.1", [osinfo.COMMAND, osinfo.WINDOWS_COMMAND]))

    def test_cisco_fallback(self):
        found, client, _ = self._detect([("% Invalid input detected at '^' marker.", 0), ("% Invalid input", 0),
                                         ("Cisco IOS XE Software, Version 17.09.04a\n", 0)])
        self.assertEqual((found, client.commands),
                         ("Cisco IOS XE Software, Version 17.09.04a",
                          [osinfo.COMMAND, osinfo.WINDOWS_COMMAND, osinfo.CISCO_COMMAND]))
        self.assertEqual(osinfo.CISCO_COMMAND, "show version | include ^Cisco IOS XE")

    def test_cisco_closes_session_on_unknown_command(self):
        """IOS XE kappt die Sitzung nach ``sh -c``: es wird neu verbunden und der Cisco-Befehl trotzdem versucht."""
        clients = []

        def make(*_a, **_k):
            client = FakeClient([("", 1), ("", 1), ("Cisco IOS XE Software, Version 17.12.1\n", 0)])
            if not clients:
                def boom(command, timeout=None):
                    client.commands.append(command)
                    raise EOFError("Channel closed")
                client.exec_command = boom
            clients.append(client)
            return client, None

        with mock.patch.object(sftp, "open_client", side_effect=make):
            found = osinfo.detect(Config(), Connection("x", "h"))
        self.assertEqual(found, "Cisco IOS XE Software, Version 17.12.1")
        self.assertTrue(all(c.closed for c in clients))

    def test_all_commands_fail_raises(self):
        def make(*_a, **_k):
            client = FakeClient([])
            client.exec_command = mock.Mock(side_effect=EOFError("Channel closed"))
            return client, None

        with mock.patch.object(sftp, "open_client", side_effect=make):
            with self.assertRaises(sftp.SftpError):
                osinfo.detect(Config(), Connection("x", "h"))

    def test_nothing_usable(self):
        found, client, _ = self._detect([("% Invalid input", 0), ("% Invalid input", 0), ("% Invalid input", 0)])
        self.assertEqual((found, client.closed), ("", True))

    def test_strict_is_passed_on(self):
        _found, _client, opened = self._detect([("Linux 6\n", 0), ("", 1)], strict=True)
        self.assertTrue(opened.call_args.kwargs["strict_host_keys"])
        _found, _client, opened = self._detect([("Linux 6\n", 0), ("", 1)])
        self.assertFalse(opened.call_args.kwargs["strict_host_keys"])

    def test_unknown_host_key_is_not_accepted_when_strict(self):
        import paramiko

        error = paramiko.SSHException("Server 'h' not found in known_hosts")
        with mock.patch.object(paramiko.SSHClient, "connect", side_effect=error):
            with self.assertRaises(sftp.SftpUnknownHostKey):
                sftp.open_client(Config(), Connection("x", "h"), strict_host_keys=True)
            with self.assertRaises(sftp.SftpError) as ctx:  # ohne strict bleibt es eine gewöhnliche Fehlermeldung
                sftp.open_client(Config(), Connection("x", "h"))
            self.assertNotIsInstance(ctx.exception, sftp.SftpUnknownHostKey)

    def test_strict_uses_reject_policy(self):
        import paramiko

        with mock.patch.object(paramiko.SSHClient, "connect", side_effect=paramiko.SSHException("x")), \
                mock.patch.object(paramiko.SSHClient, "set_missing_host_key_policy") as policy:
            with self.assertRaises(sftp.SftpError):
                sftp.open_client(Config(), Connection("x", "h"), strict_host_keys=True)
        self.assertIsInstance(policy.call_args.args[0], paramiko.RejectPolicy)


class StorageTests(unittest.TestCase):
    def test_defaults_and_junk(self):
        from sesam.storage import _from_data

        self.assertEqual(Config().hidden_columns, ["os"])  # Betriebssystem ist zunächst ausgeblendet
        self.assertEqual(_from_data({}).hidden_columns, ["os"])
        for junk in ("os", 5, None, {"os": 1}):
            self.assertEqual(_from_data({"hidden_columns": junk}).hidden_columns, ["os"], junk)
        self.assertEqual(_from_data({"hidden_columns": []}).hidden_columns, [])  # bewusst alles eingeblendet
        self.assertEqual(_from_data({"hidden_columns": ["tags", "name", 3, "terminal", "x"]}).hidden_columns, ["terminal", "tags"])

    def test_os_info_roundtrip_and_junk(self):
        data = {"connections": [{"name": "a", "host": "h", "os_info": "Debian 12"}, {"name": "b", "host": "h", "os_info": 5},
                                {"name": "c", "host": "h"}]}
        self.assertEqual([c.os_info for c in storage._from_data(data).connections], ["Debian 12", "", ""])


class ColumnTests(GuiTestCase):
    def setUp(self):
        super().setUp()
        storage.save(Config(connections=[Connection("web", "web.example.com", os_info="Debian GNU/Linux 12 (bookworm)")]), self.path)

    def test_os_column_is_hidden_by_default_and_shows_the_stored_value(self):
        w = self._window()
        self.addCleanup(w.close)
        header = w.tree.header()
        self.assertEqual(w.tree.headerItem().text(4), "Betriebssystem")
        self.assertTrue(header.isSectionHidden(4))
        self.assertFalse(any(header.isSectionHidden(i) for i in (0, 1, 2, 3)))
        item = w._find_item(w.cfg.connections[0].id)
        self.assertEqual(item.text(4), "Debian GNU/Linux 12 (bookworm)")

    def test_os_column_sits_behind_the_target(self):
        w = self._window()
        self.addCleanup(w.close)
        header = w.tree.header()
        self.assertEqual([header.logicalIndex(v) for v in range(5)], [0, 1, 4, 2, 3])

    def test_hide_terminal_show_os_and_remember(self):
        w = self._window()
        self.addCleanup(w.close)
        w.set_column_visible("terminal", False)
        w.set_column_visible("os", True)
        header = w.tree.header()
        self.assertTrue(header.isSectionHidden(2))
        self.assertFalse(header.isSectionHidden(4))
        self.assertEqual(storage.load(self.path).hidden_columns, ["terminal"])
        w2 = self._window()  # neu gestartet
        self.addCleanup(w2.close)
        self.assertTrue(w2.tree.header().isSectionHidden(2))
        self.assertFalse(w2.tree.header().isSectionHidden(4))
        w2.set_column_visible("terminal", True)
        self.assertEqual(storage.load(self.path).hidden_columns, [])

    def test_unknown_key_and_name_cannot_be_hidden(self):
        w = self._window()
        self.addCleanup(w.close)
        w.set_column_visible("name", False)
        w.set_column_visible("quatsch", False)
        self.assertFalse(w.tree.header().isSectionHidden(0))
        self.assertEqual(w.cfg.hidden_columns, ["os"])

    def test_header_menu_toggles_columns(self):
        from PySide6.QtCore import QPoint
        w = self._window()
        self.addCleanup(w.close)
        actions = {a.text(): a for a in open_menu(lambda: w._header_menu(QPoint(5, 5)))}
        self.assertEqual(list(actions), ["Ziel", "Terminal", "Tags", "Betriebssystem"])  # der Name fehlt: er bleibt immer sichtbar
        self.assertEqual([a.isChecked() for a in actions.values()], [True, True, True, False])
        actions["Betriebssystem"].toggle()
        actions["Tags"].toggle()
        self.assertEqual(w.cfg.hidden_columns, ["tags"])
        self.assertFalse(w.tree.header().isSectionHidden(4))
        self.assertTrue(w.tree.header().isSectionHidden(3))

    def test_widths_survive_hiding(self):
        w = self._window()
        self.addCleanup(w.close)
        w.tree.header().resizeSection(2, 171)
        w._flush_widths()
        w.set_column_visible("terminal", False)
        w._flush_widths()
        self.assertEqual(storage.load(self.path).column_widths[2], 171)  # nicht 0
        w2 = self._window()
        self.addCleanup(w2.close)
        w2.set_column_visible("terminal", True)
        self.assertEqual(w2.tree.header().sectionSize(2), 171)

    def test_old_widths_without_os_column_are_kept(self):
        data = json.loads(self.path.read_text(encoding="utf-8"))
        data["column_widths"] = [301, 222, 111, 150]
        self.path.write_text(json.dumps(data), encoding="utf-8")
        w = self._window()
        self.addCleanup(w.close)
        header = w.tree.header()
        self.assertEqual([header.sectionSize(i) for i in range(3)], [301, 222, 111])
        w.set_column_visible("os", True)
        self.assertEqual(header.sectionSize(4), w.DEFAULT_WIDTHS[4])


class DetectOsTests(GuiTestCase):
    def setUp(self):
        super().setUp()
        self.conn = Connection("web", "web.example.com")
        storage.save(Config(connections=[self.conn]), self.path)
        self.calls = []
        self.w = self._window()
        self.addCleanup(self.w.close)
        patcher = mock.patch("sesam.launcher.launch", lambda *a, **k: ["ssh"])  # kein echtes Terminal
        patcher.start()
        self.addCleanup(patcher.stop)

    def _wait(self, condition, seconds=3):
        deadline = time.time() + seconds
        while not condition() and time.time() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        return condition()

    def _select(self):
        self.w._select_connection(self.conn.id)

    def _detect(self, result):
        def fake(cfg, conn, password=None, known_hosts=None, timeout=10.0, strict=False):
            self.calls.append((conn.id, strict, password))
            value = result.pop(0) if isinstance(result, list) else result
            if isinstance(value, Exception):
                raise value
            return value
        return mock.patch.object(osinfo, "detect", fake)

    def test_first_connect_detects_and_stores(self):
        with self._detect("Debian GNU/Linux 12 (bookworm)"):
            self._select()
            self.w.connect_selected()
            self.assertTrue(self._wait(lambda: self.w.cfg.connection_by_id(self.conn.id).os_info))
        self.assertEqual(storage.load(self.path).connections[0].os_info, "Debian GNU/Linux 12 (bookworm)")  # bleibt für den nächsten Start
        self.assertEqual(self.w._find_item(self.conn.id).text(4), "Debian GNU/Linux 12 (bookworm)")
        self.assertEqual(self.calls, [(self.conn.id, True, None)])  # unbekannte Host-Schlüssel nie stillschweigend übernehmen

    def test_known_os_is_not_queried_again(self):
        self.w.cfg.connection_by_id(self.conn.id).os_info = "Alpine Linux v3.20"
        with self._detect("anderes"):
            self._select()
            self.w.connect_selected()
            time.sleep(0.1)
            self.app.processEvents()
        self.assertEqual(self.calls, [])

    def test_failure_is_tried_once_per_session_and_stays_quiet(self):
        with self._detect(sftp.SftpAuthError("nein")):
            self._select()
            self.w.connect_selected()
            self.assertTrue(self._wait(lambda: self.conn.id in self.w._os_tried))
            self.w.connect_selected()
            time.sleep(0.1)
            self.app.processEvents()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.w.cfg.connection_by_id(self.conn.id).os_info, "")

    def test_unknown_host_is_retried_later(self):
        self.w.OS_RETRY_MS = (30,)
        with self._detect([sftp.SftpUnknownHostKey("neu"), "Ubuntu 24.04 LTS"]):
            self._select()
            self.w.connect_selected()
            self.assertTrue(self._wait(lambda: self.w.cfg.connection_by_id(self.conn.id).os_info))
        self.assertEqual(self.w.cfg.connection_by_id(self.conn.id).os_info, "Ubuntu 24.04 LTS")
        self.assertEqual(len(self.calls), 2)

    def test_unknown_host_gives_up_after_the_last_retry(self):
        self.w.OS_RETRY_MS = (10,)
        with self._detect(sftp.SftpUnknownHostKey("neu")):
            self._select()
            self.w.connect_selected()
            self.assertTrue(self._wait(lambda: self.conn.id in self.w._os_tried))
        self.assertEqual(len(self.calls), 2)

    def test_jump_host_connections_are_skipped(self):
        self.w.cfg.connection_by_id(self.conn.id).jump_host = "bastion"
        with self._detect("x"):
            self._select()
            self.w.connect_selected()
            time.sleep(0.1)
        self.assertEqual(self.calls, [])

    def test_failed_launch_does_not_query(self):
        from PySide6.QtWidgets import QMessageBox

        from sesam import launcher

        def boom(*a, **k):
            raise launcher.LaunchError("kein Terminal")

        with self._detect("x"), mock.patch("sesam.launcher.launch", boom), mock.patch.object(QMessageBox, "critical", lambda *a: None):
            self._select()
            self.w.connect_selected()
            time.sleep(0.1)
        self.assertEqual(self.calls, [])

    def test_manual_detection_asks_for_a_password_and_reports_errors(self):
        from PySide6.QtWidgets import QInputDialog

        asked = []
        with self._detect([sftp.SftpAuthError("nein"), "Rocky Linux 9.4 (Blue Onyx)"]), \
                mock.patch.object(QInputDialog, "getText", lambda *a, **k: asked.append(1) or ("geheim", True)):
            self._select()
            self.w.detect_os_selected()
            self.assertTrue(self._wait(lambda: self.w.cfg.connection_by_id(self.conn.id).os_info))
        self.assertEqual(asked, [1])
        self.assertEqual(self.calls, [(self.conn.id, False, None), (self.conn.id, False, "geheim")])  # manuell: nicht streng
        self.w._os_tried.clear()
        with self._detect(sftp.SftpError("Zeitüberschreitung")):
            self.w.detect_os_selected()
            self.assertTrue(self._wait(lambda: "Zeitüberschreitung" in self.w.statusBar().currentMessage()))

    def test_manual_detection_replaces_an_outdated_value(self):
        self.w.cfg.connection_by_id(self.conn.id).os_info = "Debian GNU/Linux 11 (bullseye)"
        with self._detect("Debian GNU/Linux 12 (bookworm)"):
            self._select()
            self.w.detect_os_selected()
            self.assertTrue(self._wait(lambda: self.w.cfg.connection_by_id(self.conn.id).os_info.endswith("(bookworm)")))
        self.assertEqual(storage.load(self.path).connections[0].os_info, "Debian GNU/Linux 12 (bookworm)")

    def test_context_menu_offers_detection(self):
        from PySide6.QtCore import QPoint
        self._select()
        item = self.w._find_item(self.conn.id)
        pos = self.w.tree.visualItemRect(item).center()
        actions = open_menu(lambda: self.w._context_menu(pos))
        self.assertIn("Betriebssystem ermitteln", [a.text() for a in actions])


if __name__ == "__main__":
    unittest.main()
