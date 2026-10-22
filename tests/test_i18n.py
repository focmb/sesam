"""Sprachumschaltung: tr(), Katalog-Vollständigkeit, Konfiguration, Einstellungsdialog, englische Oberfläche."""
import ast
import json
import os
import re
import string
import unittest
from pathlib import Path
from unittest import mock

from gui_base import GuiTestCase

from sesam import i18n, storage
from sesam.i18n import tr
from sesam.models import Config, Connection
from sesam.translations_en import EN

SRC = Path(__file__).resolve().parent.parent / "src" / "sesam"
# im Englischen gleich geschrieben: bewusst nicht im Katalog
SAME_IN_ENGLISH = {"Name*", "Host*", "Port", "Jump-Host", "Docker", "Proxmox", "Cisco", "root", "VM", "Tags", "Terminal", "Shell", "Branch", "Orange"}


def _fields(text: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


def _literal_keys() -> dict[str, str]:
    """Alle Texte, die als Zeichenkette direkt an ``tr()`` übergeben werden → Fundstelle."""
    found = {}
    for path in sorted(SRC.rglob("*.py")):
        if path.name == "translations_en.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "tr" and node.args:
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    found.setdefault(arg.value, f"{path.name}:{node.lineno}")
    return found


class LanguageCase(unittest.TestCase):
    def setUp(self):
        self.addCleanup(i18n.set_language, "de")
        i18n.set_language("de")


class TrTests(LanguageCase):
    def test_german_is_the_source_text(self):
        self.assertEqual(tr("Löschen"), "Löschen")
        self.assertEqual(tr("{count} Verbindung(en)", count=3), "3 Verbindung(en)")

    def test_english_translation_and_fallback(self):
        i18n.set_language("en")
        self.assertEqual(tr("Löschen"), "Delete")
        self.assertEqual(tr("{count} Verbindung(en)", count=3), "3 connection(s)")
        self.assertEqual(tr("Ein Text, den es nicht gibt"), "Ein Text, den es nicht gibt")  # ohne Übersetzung bleibt das Original
        self.assertEqual(tr("Ein Text mit {text}", text="Wert"), "Ein Text mit Wert")  # „text“ ist als Platzhalter erlaubt

    def test_switching_back_to_german(self):
        i18n.set_language("en")
        i18n.set_language("de")
        self.assertEqual(tr("Löschen"), "Löschen")

    def test_unknown_setting_means_system(self):
        self.assertEqual(i18n.normalize("fr"), "system")
        self.assertEqual(i18n.normalize(None), "system")
        self.assertEqual(i18n.normalize("en"), "en")

    def test_system_language_follows_environment(self):
        for lang, expected in (("de_DE.UTF-8", "de"), ("de_AT", "de"), ("en_US.UTF-8", "en"), ("fr_FR.UTF-8", "en")):
            with self.subTest(lang=lang), mock.patch.dict(os.environ, {"LANGUAGE": "", "LC_ALL": lang, "LC_MESSAGES": "", "LANG": ""}):
                self.assertEqual(i18n.resolve("system"), expected)
        self.assertEqual(i18n.resolve("de"), "de")
        self.assertEqual(i18n.resolve("en"), "en")

    def test_saved_language_is_read_without_gui(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp, "APPDATA": tmp}):
                path = storage.config_path()
                self.assertEqual(i18n.load_saved_language() in ("de", "en"), True)  # keine Datei: Systemsprache
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps({"language": "en"}), encoding="utf-8")
                self.assertEqual(i18n.load_saved_language(), "en")
                path.write_text("kein json", encoding="utf-8")
                self.assertIn(i18n.load_saved_language(), ("de", "en"))  # beschädigt: kein Absturz


class CatalogTests(unittest.TestCase):
    def test_every_text_has_an_english_translation(self):
        missing = {k: where for k, where in _literal_keys().items() if k not in EN and k not in SAME_IN_ENGLISH}
        self.assertEqual(missing, {}, "Diese Texte fehlen in translations_en.py")

    def test_translations_keep_the_placeholders(self):
        bad = [k for k, v in EN.items() if _fields(k) != _fields(v)]
        self.assertEqual(bad, [])

    def test_no_unused_entries(self):
        from sesam.gui import sync_dialog, terminals_dialog, snippets_dialog, main_window
        from sesam import colors

        used = set(_literal_keys())
        used |= {sync_dialog.HELP, terminals_dialog.HELP, *snippets_dialog.AFTER_LABELS.values(), *main_window.MainWindow.THEME_TIPS.values(),
                 *(name for name, _ in colors.PALETTE.values()), "Eingabeaufforderung (cmd)"}
        self.assertEqual([k for k in EN if k not in used], [])

    def test_texts_assembled_from_constants_are_translated(self):
        from sesam import colors
        from sesam.gui import main_window, snippets_dialog, sync_dialog, terminals_dialog

        constants = [sync_dialog.HELP, terminals_dialog.HELP, *snippets_dialog.AFTER_LABELS.values(),
                     *main_window.MainWindow.THEME_TIPS.values(), *(name for name, _ in colors.PALETTE.values()),
                     "Eingabeaufforderung (cmd)"]
        self.assertEqual([c for c in constants if c not in EN], [])

    def test_english_has_no_german_leftovers(self):
        umlauts = re.compile(r"[äöüÄÖÜß]")
        self.assertEqual([v for v in EN.values() if umlauts.search(v)], [])

    def test_catalog_keys_are_unique_texts(self):
        self.assertEqual(len(EN), len(set(EN)))


class ConfigTests(unittest.TestCase):
    def test_language_defaults_to_system_and_is_saved(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "c.json"
            self.assertEqual(Config().language, "system")
            cfg = Config(language="en")
            storage.save(cfg, p)
            self.assertEqual(storage.load(p).language, "en")
            data = json.loads(p.read_text(encoding="utf-8"))
            for raw in ("fr", 5, None, ["en"]):
                data["language"] = raw
                p.write_text(json.dumps(data), encoding="utf-8")
                self.assertEqual(storage.load(p).language, "system", raw)
            del data["language"]  # alte Konfiguration
            p.write_text(json.dumps(data), encoding="utf-8")
            self.assertEqual(storage.load(p).language, "system")


class EnglishUiTests(GuiTestCase):
    def setUp(self):
        super().setUp()
        self.addCleanup(i18n.set_language, "de")
        storage.save(Config(connections=[Connection("a", "h")]), self.path)

    def test_settings_dialog_changes_font_size_and_language(self):
        from sesam.gui.settings_dialog import SettingsDialog

        w = self._window()
        dlg = SettingsDialog(w.cfg.font_scale, w.cfg.language, w.set_font_scale, w.set_language, w)
        self.assertEqual(dlg.windowTitle(), "Einstellungen")
        self.assertEqual([dlg.size_box.itemData(i) for i in range(dlg.size_box.count())], list(__import__("sesam.gui.theme", fromlist=["x"]).SCALE_STEPS))
        self.assertEqual(dlg.language_box.currentData(), "system")
        self.assertTrue(dlg.restart_hint.isHidden())
        dlg.size_box.setCurrentIndex(dlg.size_box.findData(150))
        self.assertEqual(w.cfg.font_scale, 150)
        self.assertEqual(storage.load(self.path).font_scale, 150)
        dlg.language_box.setCurrentIndex(dlg.language_box.findData("en"))
        self.assertEqual(w.cfg.language, "en")
        self.assertEqual(storage.load(self.path).language, "en")
        self.assertFalse(dlg.restart_hint.isHidden())  # Sprache gilt erst nach dem Neustart
        self.assertEqual(i18n.language(), "de")  # laufende Sitzung bleibt unverändert
        dlg.language_box.setCurrentIndex(dlg.language_box.findData("system"))
        self.assertTrue(dlg.restart_hint.isHidden())
        dlg.size_box.setCurrentIndex(dlg.size_box.findData(100))
        self.assertEqual(w.cfg.font_scale, 100)

    def test_settings_dialog_reset_button_and_unusual_scale(self):
        from sesam.gui.settings_dialog import SettingsDialog

        seen = []
        dlg = SettingsDialog(119, "de", seen.append, lambda c: None)
        self.assertEqual(dlg.size_box.currentData(), 119)  # ungewöhnlicher Wert bleibt auswählbar
        self.assertEqual(dlg.language_box.currentData(), "de")
        dlg.findChildren(__import__("PySide6.QtWidgets", fromlist=["x"]).QPushButton)[0].click()
        self.assertEqual(seen[-1], 100)

    def test_main_window_speaks_english(self):
        i18n.set_language("en")
        w = self._window()
        self.assertEqual(w.side_buttons["a_connect"].text(), "Connect")
        self.assertEqual(w.side_buttons["a_new"].text(), "New connection")
        self.assertEqual(w.side_buttons["a_del"].text(), "Delete")
        self.assertEqual(w.side_menu_buttons["settings"].text(), "Settings")
        self.assertEqual(w.theme_buttons["light"].toolTip(), "Light")
        self.assertEqual(w.theme_buttons["dark"].toolTip(), "Dark")
        self.assertEqual(w.theme_buttons["system"].toolTip(), "Same as system")
        self.assertEqual(w.tree.headerItem().text(1), "Target")
        self.assertEqual(w.search.placeholderText(), "Search (name, host, user, group) …")
        self.assertIn("Version", w.version_label.text())

    def test_dialogs_speak_english(self):
        from sesam.gui.connection_dialog import ConnectionDialog
        from sesam.gui.keys_dialog import KeysDialog
        from sesam.gui.settings_dialog import SettingsDialog
        from sesam.gui.snippets_dialog import SnippetsDialog
        from sesam.gui.sync_dialog import SyncDialog
        from sesam.gui.terminals_dialog import TerminalsDialog

        i18n.set_language("en")
        cfg = storage.load(self.path)
        dlgs = [ConnectionDialog(cfg), SyncDialog(cfg), TerminalsDialog(cfg), SettingsDialog(100, "system", lambda v: None, lambda c: None),
                KeysDialog(cfg, lambda: None), SnippetsDialog(cfg, lambda: None, lambda *a: None, [])]
        umlauts = re.compile(r"[äöüÄÖÜß]")
        from PySide6.QtWidgets import QAbstractButton, QLabel
        for dlg in dlgs:
            texts = [dlg.windowTitle(), *(w.text() for w in dlg.findChildren(QLabel)), *(w.text() for w in dlg.findChildren(QAbstractButton))]
            for text in texts:
                self.assertIsNone(umlauts.search(text), f"{type(dlg).__name__}: {text!r}")

    def test_english_formats_for_sizes_and_dates(self):
        from sesam.gui import sftp_window

        self.assertEqual(sftp_window.format_size(1536), "1,5 KB")
        i18n.set_language("en")
        self.assertEqual(sftp_window.format_size(1536), "1.5 KB")
        self.assertRegex(sftp_window.format_time(86400 * 365), r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")

    def test_error_messages_follow_the_language(self):
        from sesam import groups

        cfg = Config()
        with self.assertRaises(ValueError) as ctx:
            groups.rename_group(cfg, "x", "")
        self.assertEqual(str(ctx.exception), "Der Gruppenname darf nicht leer sein.")
        i18n.set_language("en")
        with self.assertRaises(ValueError) as ctx_en:
            groups.rename_group(cfg, "x", "")
        self.assertNotEqual(str(ctx.exception), str(ctx_en.exception))
        self.assertEqual(str(ctx_en.exception), "The group name must not be empty.")


if __name__ == "__main__":
    unittest.main()
