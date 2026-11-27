"""Farbschemas: Werte, Ableitung, Auswahl in den Einstellungen, Terminalfarben, Konfiguration."""
import json
import unittest
from unittest import mock

from gui_base import GuiTestCase

from sesam import schemes, storage
from sesam.models import Config

TOKEN_KEYS = set(schemes.tokens(schemes.SESAM_LIGHT))


class SchemeDataTests(unittest.TestCase):
    def test_ids_are_unique_and_names_are_set(self):
        ids = [s.id for s in schemes.BUILTIN]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(ids[:2], ["sesam-light", "sesam-dark"])
        self.assertEqual(len(ids), 10)
        self.assertTrue(all(s.name and s.base in ("light", "dark") for s in schemes.BUILTIN))

    def test_all_colors_are_valid_hex_and_complete(self):
        for s in schemes.BUILTIN:
            with self.subTest(scheme=s.id):
                self.assertEqual(set(s.ui), set(schemes.UI_KEYS))
                self.assertTrue(all(schemes.is_hex(v) for v in s.ui.values()))
                self.assertTrue(all(schemes.is_hex(s.terminal[k]) for k in schemes.TERMINAL_KEYS))
                self.assertEqual(len(s.terminal["ansi"]), schemes.ANSI_COUNT)
                self.assertTrue(all(schemes.is_hex(c) for c in s.terminal["ansi"]))
                self.assertEqual(set(schemes.tokens(s)), TOKEN_KEYS)

    def test_sesam_schemes_keep_their_look(self):
        from sesam.gui import style

        self.assertEqual(schemes.tokens(schemes.SESAM_LIGHT), style.TOKENS["light"])
        self.assertEqual(schemes.tokens(schemes.SESAM_DARK), style.TOKENS["dark"])
        self.assertEqual(style.TOKENS["light"]["accent"], "#3a6cf0")
        self.assertEqual(style.TOKENS["dark"]["bg"], "#1a1c22")

    def test_text_stays_readable_in_every_scheme(self):
        for s in schemes.BUILTIN:
            t = schemes.tokens(s)
            with self.subTest(scheme=s.id):
                for fg, bgs, minimum in (("text", ("bg", "surface"), 4.5), ("muted", ("bg", "surface"), 4.5),
                                         ("ok", ("surface",), 4.5), ("warn", ("surface",), 4.5), ("danger", ("surface",), 4.5),
                                         ("accent", ("surface",), 3.0)):
                    for bg in bgs:
                        self.assertGreaterEqual(schemes.contrast(t[fg], t[bg]), minimum, f"{fg} auf {bg}")
                self.assertGreaterEqual(schemes.contrast(t["accent_text"], t["accent"]), 4.5)
                # Terminal: Solarized hell hat von Haus aus nur gut 4:1
                self.assertGreaterEqual(schemes.contrast(s.terminal["fg"], s.terminal["bg"]), 4.0)

    def test_derivation_repairs_weak_colors(self):
        weak = {"bg": "#ffffff", "surface": "#ffffff", "text": "#202020", "accent": "#ffff00", "ok": "#99ff99", "warn": "#ffff66", "danger": "#ff9999"}
        t = schemes.derive(weak, "light", "#ff00ff")
        for key in ("ok", "warn", "danger", "muted", "badge_rdp"):
            self.assertGreaterEqual(schemes.contrast(t[key], "#ffffff"), 4.5, key)
        self.assertGreaterEqual(schemes.contrast(t["accent"], "#ffffff"), 3.0)

    def test_mode_mapping(self):
        self.assertEqual(schemes.for_mode("light").id, "sesam-light")
        self.assertEqual(schemes.for_mode("dark").id, "sesam-dark")
        self.assertEqual(schemes.for_mode("scheme:monokai").id, "monokai")
        for mode in ("system", "scheme:gibt-es-nicht", "", None, 5):
            self.assertIsNone(schemes.for_mode(mode))
        for s in schemes.BUILTIN:
            self.assertIs(schemes.for_mode(schemes.mode_for(s)), s)
        self.assertEqual(schemes.mode_for(schemes.SESAM_DARK), "dark")
        self.assertEqual(schemes.mode_for(schemes.NORD), "scheme:nord")

    def test_normalize_mode(self):
        for good in ("system", "light", "dark", "scheme:monokai", "scheme:eigenes"):
            self.assertEqual(schemes.normalize_mode(good), good)
        for bad in (None, "", "scheme:", "blau", 3, ["dark"], {"a": 1}):
            self.assertEqual(schemes.normalize_mode(bad), "system")


class ConfigTests(unittest.TestCase):
    def roundtrip(self, **fields):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.json"
            path.write_text(json.dumps(fields), encoding="utf-8")
            return storage.load(path)

    def test_old_configuration_gets_defaults(self):
        cfg = self.roundtrip()
        self.assertEqual(cfg.theme, "system")
        self.assertTrue(cfg.terminal_scheme)

    def test_scheme_and_terminal_flag_are_kept(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.json"
            storage.save(Config(theme="scheme:dracula", terminal_scheme=False), path)
            cfg = storage.load(path)
        self.assertEqual(cfg.theme, "scheme:dracula")
        self.assertFalse(cfg.terminal_scheme)

    def test_broken_values_fall_back(self):
        cfg = self.roundtrip(theme=["x"], terminal_scheme="nein")
        self.assertEqual(cfg.theme, "system")
        self.assertTrue(cfg.terminal_scheme)
        self.assertEqual(self.roundtrip(theme="scheme:").theme, "system")


class ThemeTests(GuiTestCase):
    def tearDown(self):
        from sesam.gui import theme

        theme.set_terminal_follow(True)
        theme.apply(self.app, "system")

    def test_every_scheme_applies_with_a_fully_resolved_stylesheet(self):
        from sesam.gui import theme

        for s in schemes.BUILTIN:
            with self.subTest(scheme=s.id):
                self.assertEqual(theme.apply(self.app, schemes.mode_for(s)), s.base)
                self.assertNotIn("@", self.app.styleSheet())
                self.assertIn(schemes.tokens(s)["accent"], self.app.styleSheet())
                self.assertEqual(theme.colors()["bg"], schemes.tokens(s)["bg"])
                self.assertIs(theme.scheme(), s)
                self.assertEqual(theme.is_dark(), s.is_dark)

    def test_unknown_scheme_means_system(self):
        from sesam.gui import theme

        theme.apply(self.app, "scheme:gibt-es-nicht")
        self.assertIn(theme.scheme().id, ("sesam-light", "sesam-dark"))

    def test_terminal_colors_follow_the_scheme_or_stay_default(self):
        from sesam.gui import theme

        theme.apply(self.app, "scheme:monokai")
        theme.set_terminal_follow(True)
        self.assertEqual(theme.terminal_colors()["bg"], schemes.MONOKAI.terminal["bg"])
        theme.set_terminal_follow(False)
        self.assertEqual(theme.terminal_colors(), schemes.SESAM_DARK.terminal)  # Monokai ist dunkel
        theme.apply(self.app, "scheme:solarized-light")
        self.assertEqual(theme.terminal_colors(), schemes.SESAM_LIGHT.terminal)

    def test_switching_the_terminal_flag_notifies(self):
        from sesam.gui import theme

        seen = []
        theme.notifier().changed.connect(lambda: seen.append(1))
        theme.set_terminal_follow(True)  # unverändert: keine Meldung
        self.assertEqual(seen, [])
        theme.set_terminal_follow(False)
        self.assertEqual(seen, [1])

    def test_terminal_view_takes_scheme_colors(self):
        from sesam.gui.terminal_view import SCHEMES, TerminalView

        view = TerminalView("x", 100, True, scheme=schemes.NORD.terminal)
        self.assertEqual(view._scheme["bg"], schemes.NORD.terminal["bg"])
        view.set_dark(False)
        self.assertEqual(view._scheme, SCHEMES["light"])
        view.set_dark(True, schemes.DRACULA.terminal)
        self.assertEqual(view._scheme["bg"], schemes.DRACULA.terminal["bg"])


class SettingsTests(GuiTestCase):
    def tearDown(self):
        from sesam.gui import theme

        theme.set_terminal_follow(True)
        theme.apply(self.app, "system")

    def test_scheme_list_selects_and_reports(self):
        from sesam.gui.settings_dialog import SettingsDialog

        seen = []
        dlg = SettingsDialog(100, "system", lambda v: None, lambda c: None, theme_mode="scheme:nord", set_theme=seen.append)
        box = dlg.scheme_box
        self.assertEqual(box.currentData(), "scheme:nord")
        datas = [box.itemData(i) for i in range(box.count()) if box.itemData(i)]
        self.assertEqual(datas[:3], ["system", "light", "dark"])
        self.assertEqual(len(datas), 1 + len(schemes.BUILTIN))
        box.setCurrentIndex(box.findData("scheme:monokai"))
        box.setCurrentIndex(box.findData("dark"))
        self.assertEqual(seen, ["scheme:monokai", "dark"])

    def test_unknown_saved_scheme_shows_system(self):
        from sesam.gui.settings_dialog import SettingsDialog

        dlg = SettingsDialog(100, "system", lambda v: None, lambda c: None, theme_mode="scheme:weg", set_theme=lambda m: None)
        self.assertEqual(dlg.scheme_box.currentData(), "system")

    def test_terminal_checkbox(self):
        from sesam.gui.settings_dialog import SettingsDialog

        seen = []
        dlg = SettingsDialog(100, "system", lambda v: None, lambda c: None, terminal_scheme=True, set_terminal_scheme=seen.append)
        self.assertTrue(dlg.terminal_scheme_box.isChecked())
        dlg.terminal_scheme_box.setChecked(False)
        self.assertEqual(seen, [False])

    def test_disabled_without_callbacks(self):
        from sesam.gui.settings_dialog import SettingsDialog

        dlg = SettingsDialog(100, "system", lambda v: None, lambda c: None)
        self.assertFalse(dlg.scheme_box.isEnabled())
        self.assertFalse(dlg.terminal_scheme_box.isEnabled())


class MainWindowTests(GuiTestCase):
    def tearDown(self):
        from sesam.gui import theme

        theme.set_terminal_follow(True)
        theme.apply(self.app, "system")

    def test_choosing_a_scheme_recolors_terminals_and_is_saved(self):
        from sesam.gui.terminal_view import TerminalView

        storage.save(Config(), self.path)
        w = self._window()
        view = TerminalView("x", 100, False, scheme=schemes.SESAM_LIGHT.terminal)
        with mock.patch.object(w, "_terminal_views", return_value=[view]):
            w.set_theme("scheme:dracula")
            self.assertEqual(view._scheme["bg"], schemes.DRACULA.terminal["bg"])
            w.set_terminal_scheme(False)
            self.assertEqual(view._scheme, schemes.SESAM_DARK.terminal)
            w.set_terminal_scheme(True)
            self.assertEqual(view._scheme["bg"], schemes.DRACULA.terminal["bg"])
        w.set_terminal_scheme(False)
        saved = storage.load(self.path)
        self.assertEqual(saved.theme, "scheme:dracula")
        self.assertFalse(saved.terminal_scheme)

    def test_appearance_menu_has_no_check_for_a_scheme(self):
        storage.save(Config(), self.path)
        w = self._window()
        w.set_theme("dark")
        self.assertTrue(w.theme_actions["dark"].isChecked())
        w.set_theme("scheme:nord")
        self.assertFalse(any(a.isChecked() for a in w.theme_actions.values()))
        w.set_theme("light")
        self.assertTrue(w.theme_actions["light"].isChecked())

    def test_settings_dialog_gets_the_current_scheme(self):
        storage.save(Config(theme="scheme:tokyo-night", terminal_scheme=False), self.path)
        w = self._window()
        seen = {}

        def fake_exec(dlg):
            seen["mode"] = dlg.scheme_box.currentData()
            seen["terminal"] = dlg.terminal_scheme_box.isChecked()
            return 0

        from PySide6.QtWidgets import QDialog

        with mock.patch.object(QDialog, "exec", fake_exec):
            w.edit_settings()
        self.assertEqual(seen, {"mode": "scheme:tokyo-night", "terminal": False})


if __name__ == "__main__":
    unittest.main()
