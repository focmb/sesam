"""Verhalten bei ungültiger config.json: nie löschen, sichern, verständlich melden."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

from sesam import storage

try:
    from PySide6.QtWidgets import QMessageBox
except ImportError:  # PySide6 oder Qt-Systembibliotheken fehlen
    QMessageBox = None


class StorageRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "config.json"

    def test_invalid_contents_raise_config_error(self):
        cases = {
            "leer": b"",
            "kein JSON": b"{kaputt",
            "abgeschnitten": b'{"connections": [{"name": "a"',
            "Liste statt Objekt": b"[1, 2]",
            "Verbindungen als Text": b'{"connections": "x"}',
            "Verbindung kein Objekt": b'{"connections": [1]}',
            "kein UTF-8": b"\xff\xfe\x00{",
        }
        for label, raw in cases.items():
            with self.subTest(label):
                self.path.write_bytes(raw)
                with self.assertRaises(storage.ConfigError) as ctx:
                    storage.load(self.path)
                self.assertEqual(ctx.exception.path, self.path)
                self.assertTrue(ctx.exception.reason)
                self.assertEqual(self.path.read_bytes(), raw)  # load verändert nichts

    def test_list_instead_of_object_has_clear_reason(self):
        self.path.write_text("[1, 2]", encoding="utf-8")
        with self.assertRaises(storage.ConfigError) as ctx:
            storage.load(self.path)
        self.assertIn("JSON-Objekt", ctx.exception.reason)

    def test_json_error_reason_names_position(self):
        self.path.write_text('{\n  "a": ,\n}', encoding="utf-8")
        with self.assertRaises(storage.ConfigError) as ctx:
            storage.load(self.path)
        self.assertIn("Zeile 2", ctx.exception.reason)

    def test_valid_and_missing_files_still_load(self):
        self.assertTrue(storage.load(self.path).terminals)  # fehlende Datei: Standardwerte
        self.path.write_text(json.dumps({"connections": [{"name": "a", "host": "h"}]}), encoding="utf-8")
        self.assertEqual([c.name for c in storage.load(self.path).connections], ["a"])

    def test_quarantine_moves_and_never_overwrites(self):
        self.path.write_bytes(b"eins")
        first = storage.quarantine(self.path)
        self.assertFalse(self.path.exists())
        self.assertEqual(first.read_bytes(), b"eins")
        self.assertIn(".defekt-", first.name)
        self.path.write_bytes(b"zwei")
        with mock.patch.object(storage, "datetime") as dt:  # gleiche Sekunde -> anderer Name
            dt.now.return_value.strftime.return_value = first.name.split(".defekt-")[1]
            second = storage.quarantine(self.path)
        self.assertNotEqual(first, second)
        self.assertEqual(first.read_bytes(), b"eins")
        self.assertEqual(second.read_bytes(), b"zwei")


class ConnectCliTests(unittest.TestCase):
    def test_wrapper_reports_broken_config_instead_of_traceback(self):
        from sesam import connect_cli

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text("{kaputt", encoding="utf-8")
            err = io.StringIO()
            with mock.patch.object(storage, "config_path", return_value=path), \
                    mock.patch("builtins.input", return_value=""), redirect_stderr(err):
                rc = connect_cli.run("irgendeine-id")
            self.assertEqual(rc, 1)
            self.assertIn("beschädigt", err.getvalue())
            self.assertIn(str(path), err.getvalue())
            self.assertEqual(path.read_text(encoding="utf-8"), "{kaputt")  # nichts verändert


@unittest.skipIf(QMessageBox is None, "PySide6 nicht verfügbar")
class StartupRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "config.json"
        self.warning = mock.patch.object(QMessageBox, "warning").start()
        self.critical = mock.patch.object(QMessageBox, "critical").start()
        self.addCleanup(mock.patch.stopall)

    def test_valid_config_starts_silently(self):
        from sesam.__main__ import _load_config

        self.path.write_text(json.dumps({"connections": [{"name": "a", "host": "h"}]}), encoding="utf-8")
        cfg = _load_config(self.path)
        self.assertEqual([c.name for c in cfg.connections], ["a"])
        self.warning.assert_not_called()
        self.critical.assert_not_called()

    def test_broken_config_is_backed_up_and_start_continues_empty(self):
        from sesam.__main__ import _load_config

        self.path.write_text('{"connections": [', encoding="utf-8")
        cfg = _load_config(self.path)
        self.assertIsNotNone(cfg)
        self.assertEqual(cfg.connections, [])
        self.assertTrue(cfg.terminals)
        backups = list(Path(self.tmp.name).glob("config.json.defekt-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(encoding="utf-8"), '{"connections": [')
        self.assertFalse(self.path.exists())  # nichts wird erzeugt, bis etwas gespeichert wird
        self.warning.assert_called_once()
        text = self.warning.call_args.args[2]
        self.assertIn(str(backups[0]), text)
        self.assertIn("Zeile 1", text)
        self.critical.assert_not_called()

    def test_unreadable_file_stops_and_changes_nothing(self):
        from sesam.__main__ import _load_config

        self.path.write_text("{}", encoding="utf-8")
        with mock.patch.object(storage, "load", side_effect=PermissionError("Zugriff verweigert")):
            self.assertIsNone(_load_config(self.path))
        self.critical.assert_called_once()
        self.assertEqual(self.path.read_text(encoding="utf-8"), "{}")
        self.assertEqual(list(Path(self.tmp.name).glob("*.defekt-*")), [])

    def test_backup_failure_stops_and_keeps_original(self):
        from sesam.__main__ import _load_config

        self.path.write_text("{kaputt", encoding="utf-8")
        with mock.patch.object(storage, "quarantine", side_effect=OSError("Schreibschutz")):
            self.assertIsNone(_load_config(self.path))
        self.critical.assert_called_once()
        self.assertIn("Schreibschutz", self.critical.call_args.args[2])
        self.assertEqual(self.path.read_text(encoding="utf-8"), "{kaputt")  # Original bleibt


if __name__ == "__main__":
    unittest.main()
