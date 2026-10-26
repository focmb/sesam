"""WireGuard-Anzeige: Statuserkennung (Linux/Windows, ohne echtes System) und Pille in der Statuszeile."""
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from gui_base import GuiTestCase

from sesam import i18n, storage, wireguard
from sesam.models import Config
from sesam.wireguard import CONNECTED, NEVER, OFF, STALE, UNKNOWN, Status, Tunnel

NOW = 1_800_000_000
DUMP = "\n".join([
    "PRIVATE\tPUBLIC\t51820\toff",  # Schnittstelle: der private Schlüssel wird nie ausgewertet
    f"PEERA\t(none)\t203.0.113.7:51820\t10.8.0.0/24,192.168.0.0/16\t{NOW - 14}\t3355443\t1153433\t25",
    f"PEERB\t(none)\t(none)\t10.9.0.0/24\t{NOW - 500}\t10\t20\toff",
])


def _fake_sysfs(root: Path):
    for name, uevent, flags in (
        ("wg0", "DEVTYPE=wireguard\nINTERFACE=wg0\n", "0x1091"),  # aktiv
        ("wg1", "DEVTYPE=wireguard\n", "0x1090"),  # vorhanden, aber nicht aktiv
        ("eth0", "INTERFACE=eth0\n", "0x1003"),  # keine WireGuard-Schnittstelle
        ("lo", "", "0x9"),
    ):
        (root / name).mkdir()
        (root / name / "uevent").write_text(uevent)
        (root / name / "flags").write_text(flags + "\n")


class DetectionTests(unittest.TestCase):
    def test_linux_finds_only_active_wireguard_interfaces(self):
        with tempfile.TemporaryDirectory() as tmp:
            _fake_sysfs(Path(tmp))
            self.assertEqual(wireguard.linux_interfaces(Path(tmp)), ["wg0"])

    def test_linux_unreadable_system_is_unknown_not_off(self):
        self.assertIsNone(wireguard.linux_interfaces(Path("/nonexistent/net")))

    def test_dump_is_parsed_without_the_private_key(self):
        t = wireguard.parse_dump(DUMP, now=NOW)
        self.assertEqual(t.endpoint, "203.0.113.7:51820")
        self.assertEqual(t.allowed_ips, ["10.8.0.0/24", "192.168.0.0/16", "10.9.0.0/24"])
        self.assertEqual(t.handshake_age, 14)  # die jüngste Gegenstelle zählt
        self.assertEqual((t.rx, t.tx), (3355443 + 10, 1153433 + 20))
        self.assertNotIn("PRIVATE", repr(t))

    def test_peer_without_handshake_is_never(self):
        t = wireguard.parse_dump(f"K\tP\t1\toff\nPEER\t(none)\t(none)\t10.0.0.0/8\t0\t0\t0\toff", now=NOW)
        self.assertEqual(t.handshake_age, NEVER)
        self.assertTrue(t.stale)

    def test_garbage_dump_gives_nothing(self):
        self.assertIsNone(wireguard.parse_dump(""))
        self.assertIsNone(wireguard.parse_dump("nur eine Zeile"))
        self.assertIsNone(wireguard.parse_dump("K\tP\t1\toff\nkaputt\tzeile"))

    def test_state_from_tunnels(self):
        fresh, old, unknown = Tunnel("a", 10), Tunnel("b", wireguard.STALE_AFTER + 1), Tunnel("c")
        self.assertEqual(wireguard.status_of([]).state, OFF)
        self.assertEqual(wireguard.status_of([fresh]).state, CONNECTED)
        self.assertEqual(wireguard.status_of([old]).state, STALE)
        self.assertEqual(wireguard.status_of([old, fresh]).state, CONNECTED)  # nur „veraltet“, wenn alle es sind
        self.assertEqual(wireguard.status_of([unknown]).state, CONNECTED)  # ohne wg-Rechte gilt: Tunnel läuft

    def test_detect_on_linux(self):
        with mock.patch.object(wireguard, "IS_WINDOWS", False), mock.patch.object(wireguard.sys, "platform", "linux"), \
                mock.patch.object(wireguard, "linux_interfaces", return_value=["VZS"]), \
                mock.patch.object(wireguard, "_details", return_value=Tunnel("x", 5, endpoint="vpn.example.com:51820")), \
                mock.patch.object(wireguard, "_linux_addresses", return_value=["10.8.0.12/24"]):
            status = wireguard.detect()
        self.assertEqual(status.state, CONNECTED)
        self.assertEqual(status.tunnels[0].name, "VZS")  # der Name kommt von der Schnittstelle
        self.assertEqual(status.tunnels[0].addresses, ["10.8.0.12/24"])
        self.assertEqual(status.tunnels[0].endpoint, "vpn.example.com:51820")

    def test_detect_without_wg_rights_still_sees_the_tunnel(self):
        with mock.patch.object(wireguard, "IS_WINDOWS", False), mock.patch.object(wireguard.sys, "platform", "linux"), \
                mock.patch.object(wireguard, "linux_interfaces", return_value=["VZS"]), \
                mock.patch.object(wireguard, "_details", return_value=None), \
                mock.patch.object(wireguard, "_linux_addresses", return_value=[]):
            status = wireguard.detect()
        self.assertEqual((status.state, status.tunnels[0].name, status.tunnels[0].has_details), (CONNECTED, "VZS", False))

    def test_detect_off_unknown_and_errors(self):
        with mock.patch.object(wireguard, "IS_WINDOWS", False), mock.patch.object(wireguard.sys, "platform", "linux"):
            with mock.patch.object(wireguard, "linux_interfaces", return_value=[]):
                self.assertEqual(wireguard.detect().state, OFF)
            with mock.patch.object(wireguard, "linux_interfaces", return_value=None):
                self.assertEqual(wireguard.detect().state, UNKNOWN)
            with mock.patch.object(wireguard, "linux_interfaces", side_effect=RuntimeError("boom")):
                self.assertEqual(wireguard.detect().state, UNKNOWN)  # wirft nie
        with mock.patch.object(wireguard, "IS_WINDOWS", False), mock.patch.object(wireguard.sys, "platform", "darwin"):
            self.assertEqual(wireguard.detect().state, UNKNOWN)

    def test_windows_adapters(self):
        cases = {'{"Name":"VZS"}': ["VZS"], '[{"Name":"a"},{"Name":"b"}]': ["a", "b"], "": [], "  \n": [], "kein json": None}
        for out, expected in cases.items():
            with mock.patch.object(wireguard, "_run", return_value=out):
                self.assertEqual(wireguard.windows_adapters(), expected, out)
        with mock.patch.object(wireguard, "_run", return_value=None):  # PowerShell fehlt oder scheitert
            self.assertIsNone(wireguard.windows_adapters())

    def test_detect_on_windows(self):
        with mock.patch.object(wireguard, "IS_WINDOWS", True), mock.patch.object(wireguard, "windows_adapters", return_value=["VZS"]), \
                mock.patch.object(wireguard, "_details", return_value=None):
            status = wireguard.detect()
        self.assertEqual((status.state, status.tunnels[0].name), (CONNECTED, "VZS"))

    def test_run_survives_missing_program_and_timeout(self):
        self.assertIsNone(wireguard._run(["/nonexistent/programm"]))
        with mock.patch.object(wireguard.subprocess, "run", side_effect=wireguard.subprocess.TimeoutExpired("x", 1)):
            self.assertIsNone(wireguard._run(["wg"]))


class IndicatorTests(GuiTestCase):
    def setUp(self):
        super().setUp()
        from sesam.gui.wireguard_indicator import WireGuardIndicator

        self.addCleanup(i18n.set_language, "de")
        self.ind = WireGuardIndicator()
        self.addCleanup(self.ind.deleteLater)

    def _connected(self, *names, age=14):
        return Status(CONNECTED, [Tunnel(n, age, endpoint="vpn.example.com:51820", addresses=["10.8.0.12/24"],
                                         allowed_ips=["10.8.0.0/24"], rx=3355443, tx=1153433) for n in names])

    def test_hidden_until_first_status(self):
        self.assertFalse(self.ind.isVisible())
        self.assertFalse(self.ind.isVisibleTo(None) and self.ind.isHidden() is False)
        self.ind.set_status(Status(OFF, []))
        self.assertFalse(self.ind.isHidden())

    def test_texts_and_state_property(self):
        for status, text, state in (
            (self._connected("VZS"), "WireGuard · VZS · verbunden", "connected"),
            (self._connected("VZS", "Büro"), "WireGuard · VZS +1 · verbunden", "connected"),
            (Status(STALE, [Tunnel("VZS", 600)]), "WireGuard · VZS · veraltet", "stale"),
            (Status(UNKNOWN, []), "WireGuard · nicht verfügbar", "unknown"),
        ):
            self.ind.set_status(status)
            self.assertEqual((self.ind.text(), self.ind.property("wgState")), (text, state))
            self.assertFalse(self.ind.icon().isNull())

    def test_disconnected_remembers_the_last_tunnel_name(self):
        self.ind.set_status(Status(OFF, []))
        self.assertEqual(self.ind.text(), "WireGuard · getrennt")  # noch nie einen Tunnel gesehen
        self.ind.set_status(self._connected("VZS"))
        self.ind.set_status(Status(OFF, []))
        self.assertEqual(self.ind.text(), "WireGuard · VZS · getrennt")

    def test_english(self):
        i18n.set_language("en")
        self.ind.set_status(self._connected("VZS"))
        self.assertEqual(self.ind.text(), "WireGuard · VZS · connected")
        self.ind.set_status(Status(OFF, []))
        self.assertEqual(self.ind.text(), "WireGuard · VZS · disconnected")

    def test_click_opens_details_above_the_pill(self):
        from PySide6.QtWidgets import QLabel

        self.ind.show()
        self.ind.set_status(self._connected("VZS"))
        self.ind.click()
        popup = self.ind._popup
        self.assertTrue(popup.isVisible())
        texts = [label.text() for label in popup.findChildren(QLabel)]
        for expected in ("WireGuard-Tunnel aktiv", "VZS", "vpn.example.com:51820", "10.8.0.12/24", "10.8.0.0/24", "vor 14 s", "3.2 MB / 1.1 MB"):
            self.assertTrue(any(expected in t for t in texts) or expected.replace(".", ",") in " ".join(texts), f"{expected!r} fehlt: {texts}")
        self.assertLessEqual(popup.geometry().bottom(), self.ind.mapToGlobal(self.ind.rect().topLeft()).y())
        popup.close()

    def test_details_without_wg_rights_explain_why(self):
        from PySide6.QtWidgets import QLabel

        self.ind.set_status(Status(CONNECTED, [Tunnel("VZS")]))
        self.ind.click()
        text = " ".join(label.text() for label in self.ind._popup.findChildren(QLabel))
        self.assertIn("Administratorrechten", text)
        self.assertNotIn("Letzter Handshake", text)
        self.ind._popup.close()

    def test_details_for_other_states(self):
        from PySide6.QtWidgets import QLabel

        for status, expected in ((Status(OFF, []), "Kein WireGuard-Tunnel aktiv"), (Status(UNKNOWN, []), "nicht verfügbar"),
                                 (Status(STALE, [Tunnel("VZS", NEVER)]), "noch keiner")):
            self.ind.set_status(status)
            self.ind.click()
            text = " ".join(label.text() for label in self.ind._popup.findChildren(QLabel))
            self.assertIn(expected, text)
            self.ind._popup.close()

    def test_age_texts(self):
        from sesam.gui.wireguard_indicator import age_text

        self.assertEqual([age_text(a) for a in (5, 59.9, 60, 3599, 7200, NEVER)],
                         ["vor 5 s", "vor 59 s", "vor 1 min", "vor 59 min", "vor 2 h", "noch keiner"])

    def test_refresh_runs_in_the_background_and_does_not_overlap(self):
        calls = []
        gate = __import__("threading").Event()

        def slow():
            calls.append(1)
            gate.wait(2)
            return Status(OFF, [])

        with mock.patch.object(wireguard, "detect", slow):
            self.ind.refresh()
            self.ind.refresh()  # läuft noch: keine zweite Abfrage
            time.sleep(0.1)
            self.assertEqual(len(calls), 1)
            gate.set()
            deadline = time.time() + 3
            while self.ind.isHidden() and time.time() < deadline:
                self.app.processEvents()
                time.sleep(0.01)
        self.assertFalse(self.ind.isHidden())

    def test_stopped_indicator_ignores_late_results(self):
        self.ind.stop()
        self.ind.set_status(self._connected("VZS"))
        self.assertTrue(self.ind.isHidden())


class MainWindowTests(GuiTestCase):
    def setUp(self):
        super().setUp()
        storage.save(Config(wireguard=True), self.path)  # die Anzeige ist standardmäßig aus
        self.w = self._window()
        self.addCleanup(self.w.close)

    def _wait_visible(self):
        deadline = time.time() + 3
        while self.w.wg_indicator.isHidden() and time.time() < deadline:
            self.app.processEvents()
            time.sleep(0.01)

    def test_indicator_sits_at_the_right_end_of_the_status_bar(self):
        self._wait_visible()
        w = self.w
        self.assertFalse(w.wg_indicator.isHidden())
        self.assertEqual(w.wg_indicator.text(), "WireGuard · getrennt")
        self.assertIs(w.wg_indicator.parent(), w.statusBar())
        self.assertGreater(w.wg_indicator.x(), w.sync_label.x())
        self.assertGreater(w.wg_indicator.x(), w.version_label.x())
        self.assertGreater(w.wg_indicator.mapTo(w, w.wg_indicator.rect().topLeft()).y(), w.height() - 70)  # ganz unten

    def test_polling_starts_and_stops_with_the_window(self):
        self.assertTrue(self.w.wg_indicator._timer.isActive())
        self.w.close()
        self.assertFalse(self.w.wg_indicator._timer.isActive())

    def test_pill_is_styled_per_state(self):
        from sesam.gui import style, theme

        self.assertEqual(theme.apply(self.app, "dark"), "dark")
        t = style.tokens("dark")
        qss = self.app.styleSheet()
        for key in ("ok", "ok_bg", "warn", "warn_border"):
            self.assertIn(t[key], qss)
        self.w.wg_indicator.set_status(Status(CONNECTED, [Tunnel("VZS", 3)]))
        self.app.processEvents()
        self.assertEqual(self.w.wg_indicator.property("wgState"), "connected")
        theme.apply(self.app, "system")


class SwitchTests(GuiTestCase):
    """Die Anzeige ist abschaltbar: aus (Standard) = nichts abfragen, nichts anzeigen."""

    def _wait(self, condition):
        deadline = time.time() + 3
        while not condition() and time.time() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        return condition()

    def test_off_by_default_and_old_configs_stay_off(self):
        self.assertFalse(Config().wireguard)
        storage.save(Config(), self.path)
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertIs(data["wireguard"], False)
        del data["wireguard"]  # Konfiguration aus einer älteren Version
        self.path.write_text(json.dumps(data), encoding="utf-8")
        self.assertFalse(storage.load(self.path).wireguard)
        for junk in ("ja", 1, None, [], "true"):
            data["wireguard"] = junk
            self.path.write_text(json.dumps(data), encoding="utf-8")
            self.assertFalse(storage.load(self.path).wireguard, junk)  # nur ein echtes ``true`` schaltet ein
        data["wireguard"] = True
        self.path.write_text(json.dumps(data), encoding="utf-8")
        self.assertTrue(storage.load(self.path).wireguard)

    def test_disabled_window_neither_queries_nor_shows(self):
        storage.save(Config(), self.path)
        with mock.patch.object(wireguard, "detect", side_effect=AssertionError("darf nicht abgefragt werden")) as detect:
            w = self._window()
            self.addCleanup(w.close)
            for _ in range(10):
                self.app.processEvents()
                time.sleep(0.01)
            self.assertTrue(w.wg_indicator.isHidden())
            self.assertFalse(w.wg_indicator._timer.isActive())
            detect.assert_not_called()

    def test_switching_on_and_off_in_the_window(self):
        storage.save(Config(), self.path)
        w = self._window()
        self.addCleanup(w.close)
        self.assertTrue(w.wg_indicator.isHidden())
        w.set_wireguard(True)
        self.assertTrue(storage.load(self.path).wireguard)  # wird gespeichert
        self.assertTrue(self._wait(lambda: not w.wg_indicator.isHidden()))
        self.assertTrue(w.wg_indicator._timer.isActive())
        w.set_wireguard(False)
        self.assertFalse(storage.load(self.path).wireguard)
        self.assertTrue(w.wg_indicator.isHidden())
        self.assertFalse(w.wg_indicator._timer.isActive())

    def test_switching_off_ignores_a_query_that_is_still_running(self):
        gate = threading.Event()

        def slow():
            gate.wait(2)
            return Status(OFF, [])

        storage.save(Config(), self.path)
        with mock.patch.object(wireguard, "detect", slow):
            w = self._window()
            self.addCleanup(w.close)
            w.set_wireguard(True)
            w.set_wireguard(False)  # die Antwort kommt erst jetzt und darf nichts mehr anzeigen
            gate.set()
            for _ in range(20):
                self.app.processEvents()
                time.sleep(0.01)
        self.assertTrue(w.wg_indicator.isHidden())

    def test_switching_off_closes_the_details(self):
        storage.save(Config(wireguard=True), self.path)
        w = self._window()
        self.addCleanup(w.close)
        self.assertTrue(self._wait(lambda: not w.wg_indicator.isHidden()))
        w.wg_indicator.click()
        popup = w.wg_indicator._popup
        self.assertTrue(popup.isVisible())
        w.set_wireguard(False)
        self.assertFalse(popup.isVisible())

    def test_settings_dialog_has_the_switch(self):
        from sesam.gui.settings_dialog import SettingsDialog

        seen = []
        dlg = SettingsDialog(100, "de", lambda v: None, lambda c: None, wireguard=False, set_wireguard=seen.append)
        self.addCleanup(dlg.deleteLater)
        self.assertFalse(dlg.wireguard_box.isChecked())
        dlg.wireguard_box.setChecked(True)
        dlg.wireguard_box.setChecked(False)
        self.assertEqual(seen, [True, False])
        on = SettingsDialog(100, "de", lambda v: None, lambda c: None, wireguard=True, set_wireguard=seen.append)
        self.addCleanup(on.deleteLater)
        self.assertTrue(on.wireguard_box.isChecked())

    def test_settings_dialog_from_the_window_changes_the_config(self):
        from sesam.gui.settings_dialog import SettingsDialog

        storage.save(Config(), self.path)
        w = self._window()
        self.addCleanup(w.close)
        captured = []

        def fake_exec(dlg):
            captured.append(dlg)
            dlg.wireguard_box.setChecked(True)
            return 0

        with mock.patch.object(SettingsDialog, "exec", fake_exec):
            w.edit_settings()
        self.assertTrue(captured[0].wireguard_box.isEnabled())
        self.assertTrue(w.cfg.wireguard)
        self.assertTrue(storage.load(self.path).wireguard)

    def test_english_dialog(self):
        from sesam.gui.settings_dialog import SettingsDialog

        self.addCleanup(i18n.set_language, "de")
        i18n.set_language("en")
        dlg = SettingsDialog(100, "en", lambda v: None, lambda c: None, wireguard=False, set_wireguard=lambda v: None)
        self.addCleanup(dlg.deleteLater)
        self.assertEqual(dlg.wireguard_box.text(), "Show connection status in the status bar")


if __name__ == "__main__":
    unittest.main()
