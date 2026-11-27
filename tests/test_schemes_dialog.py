"""Editor für eigene Farbschemas: Dialog, Einstellungen, Hauptfenster."""
import json
import unittest
from pathlib import Path
from unittest import mock

from gui_base import GuiTestCase

from sesam import scheme_io, schemes, storage
from sesam.models import ColorScheme, Config


def make(name="Mein Nord", source=None):
    return ColorScheme.from_scheme(source or schemes.NORD, name)


class DialogTests(GuiTestCase):
    def setUp(self):
        super().setUp()
        from sesam.gui import theme

        self.addCleanup(schemes.set_custom, [])
        self.addCleanup(lambda: theme.apply(self.app, "system"))
        self.cfg = Config(color_schemes=[make()])
        self.saved, self.themes = [], []

    def dialog(self, selected=""):
        from sesam.gui.schemes_dialog import SchemesDialog

        d = SchemesDialog(self.cfg, lambda: self.saved.append(1), self.themes.append, selected)
        self.addCleanup(d.deleteLater)
        return d

    def test_list_has_builtin_then_custom_and_starts_on_first_custom(self):
        d = self.dialog()
        self.assertEqual(d.listing.count(), len(schemes.BUILTIN) + 1)
        self.assertEqual(d._current(), self.cfg.color_schemes[0].id)
        self.assertTrue(d.name.isEnabled() and d.btn_delete.isEnabled())

    def test_builtin_is_read_only_but_can_be_duplicated(self):
        d = self.dialog()
        d.listing.setCurrentRow(0)
        self.assertFalse(d.name.isEnabled() or d.btn_delete.isEnabled() or d.ui_fields["bg"].isEnabled())
        d.duplicate()
        self.assertEqual(len(d.work), 2)
        self.assertEqual(d._current_custom().ui, schemes.SESAM_LIGHT.ui)
        self.assertTrue(d.name.isEnabled())

    def test_new_scheme_and_unique_names(self):
        d = self.dialog()
        d.new_scheme()
        d.new_scheme()
        names = [c.name for c in d.work]
        self.assertEqual(len(set(names)), 3)
        self.assertEqual(d._current_custom().base, "dark")

    def test_editing_a_color_changes_the_working_copy_only(self):
        d = self.dialog()
        field = d.ui_fields["accent"]
        field.edit.setText("ff0000")
        field.edit.textEdited.emit("ff0000")
        self.assertEqual(d._current_custom().ui["accent"], "#ff0000")
        self.assertEqual(self.cfg.color_schemes[0].ui["accent"], schemes.NORD.ui["accent"])

    def test_invalid_hex_is_not_applied(self):
        d = self.dialog()
        before = dict(d._current_custom().ui)
        d.ui_fields["bg"].edit.setText("#12")
        d.ui_fields["bg"].edit.textEdited.emit("#12")
        self.assertEqual(d._current_custom().ui, before)
        self.assertTrue(d.ui_fields["bg"].edit.styleSheet())

    def test_name_edit_updates_list_and_base_changes(self):
        d = self.dialog()
        d.name.setText("Neuer Name")
        d.name.textEdited.emit("Neuer Name")
        self.assertEqual(d.listing.currentItem().text(), "Neuer Name")
        d.base.setCurrentIndex(d.base.findData("light"))
        self.assertEqual(d._current_custom().base, "light")

    def test_contrast_warning(self):
        d = self.dialog()
        c = d._current_custom()
        self.assertEqual(d.warnings(c.to_scheme()), [])
        c.ui["text"] = c.ui["bg"]
        self.assertTrue(d.warnings(c.to_scheme()))

    def test_delete(self):
        d = self.dialog()
        d.delete()
        self.assertEqual(d.work, [])
        self.assertFalse(d.btn_delete.isEnabled())

    def test_live_preview_applies_in_the_app_and_cancel_restores(self):
        from sesam.gui import theme

        theme.apply(self.app, "dark")
        self.cfg.theme = "dark"
        d = self.dialog()
        d.ui_fields["bg"].edit.setText("#101820")
        d.ui_fields["bg"].edit.textEdited.emit("#101820")
        d._apply_preview()
        self.assertEqual(theme.scheme().id, d._current())
        self.assertEqual(theme.colors()["bg"], "#101820")
        d.reject()
        self.assertEqual(theme.scheme().id, schemes.SESAM_DARK.id)
        self.assertEqual(self.cfg.color_schemes[0].ui["bg"], schemes.NORD.ui["bg"])
        self.assertEqual(self.saved, [])

    def test_save_replaces_config_and_selects_the_scheme(self):
        d = self.dialog(selected=self.cfg.color_schemes[0].id)
        self.assertTrue(d.use_after.isChecked())
        d.duplicate()
        new_id = d._current()
        d.accept()
        self.assertEqual(len(self.cfg.color_schemes), 2)
        self.assertEqual(self.cfg.theme, "scheme:" + new_id)
        self.assertEqual(self.saved, [1])
        self.assertEqual(self.themes, ["scheme:" + new_id])
        self.assertIsNotNone(schemes.by_id(new_id))

    def test_save_without_use_keeps_theme(self):
        d = self.dialog()
        self.assertFalse(d.use_after.isChecked())
        d.accept()
        self.assertEqual(self.cfg.theme, "system")

    def test_deleting_the_active_scheme_falls_back_to_system(self):
        ident = self.cfg.color_schemes[0].id
        self.cfg.theme = "scheme:" + ident
        d = self.dialog(selected=ident)
        d.delete()
        d.use_after.setChecked(False)
        d.accept()
        self.assertEqual(self.cfg.theme, "system")
        self.assertEqual(self.cfg.color_schemes, [])
        self.assertIsNone(schemes.by_id(ident))

    def test_export_and_import(self):
        import tempfile

        d = self.dialog()
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "x.json")
            with mock.patch.object(d, "_pick_save", return_value=path):
                d.export_file()
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            self.assertEqual(data["format"], scheme_io.FORMAT)
            with mock.patch.object(d, "_pick_open", return_value=path):
                d.import_file()
        self.assertEqual(len(d.work), 2)
        self.assertEqual(d._current_custom().name, "Mein Nord (2)")
        self.assertNotEqual(d._current(), self.cfg.color_schemes[0].id)

    def test_export_of_a_builtin_scheme(self):
        import tempfile

        d = self.dialog()
        d.listing.setCurrentRow(3)
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "b.json")
            with mock.patch.object(d, "_pick_save", return_value=path):
                d.export_file()
            parsed = scheme_io.parse(Path(path).read_bytes())
        self.assertEqual(parsed.ui, schemes.by_id(d._current()).ui)

    def test_bad_import_shows_a_message_and_changes_nothing(self):
        import tempfile

        from PySide6.QtWidgets import QMessageBox

        d = self.dialog()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"
            path.write_text("{}", encoding="utf-8")
            warned = []
            with mock.patch.object(d, "_pick_open", return_value=str(path)), \
                    mock.patch.object(QMessageBox, "warning", lambda *a: warned.append(a[2])):
                d.import_file()
        self.assertEqual(len(warned), 1)
        self.assertEqual(len(d.work), 1)


class IntegrationTests(GuiTestCase):
    def tearDown(self):
        from sesam.gui import theme

        schemes.set_custom([])
        theme.apply(self.app, "system")

    def test_settings_lists_custom_schemes_and_refills_after_the_editor(self):
        from sesam.gui.settings_dialog import SettingsDialog

        cs = make()
        schemes.set_custom([cs.to_scheme()])
        chosen = []
        state = {"mode": "scheme:" + cs.id}

        def open_schemes(parent):
            other = make("Zweites", schemes.DRACULA)
            schemes.set_custom([cs.to_scheme(), other.to_scheme()])

        dlg = SettingsDialog(100, "system", lambda v: None, lambda c: None, theme_mode=state["mode"], set_theme=chosen.append,
                             open_schemes=open_schemes, get_theme=lambda: state["mode"])
        self.assertEqual(dlg.scheme_box.currentData(), "scheme:" + cs.id)
        self.assertEqual(dlg.scheme_box.itemText(dlg.scheme_box.count() - 1), "Mein Nord")
        dlg.schemes_button.click()
        self.assertEqual(dlg.scheme_box.count(), len(schemes.BUILTIN) + 1 + 2 + 2)  # + Systemvorgabe, 2 Eigene, 2 Trenner
        self.assertEqual(dlg.scheme_box.currentData(), "scheme:" + cs.id)
        self.assertEqual(chosen, [])  # das Auffüllen löst keine Auswahl aus

    def test_settings_button_disabled_without_opener(self):
        from sesam.gui.settings_dialog import SettingsDialog

        self.assertFalse(SettingsDialog(100, "system", lambda v: None, lambda c: None).schemes_button.isEnabled())

    def test_main_window_registers_the_saved_custom_schemes(self):
        from sesam.gui import theme

        cs = make()
        storage.save(Config(theme="scheme:" + cs.id, color_schemes=[cs]), self.path)
        w = self._window()
        self.assertEqual(schemes.by_id(cs.id).name, "Mein Nord")
        theme.apply(self.app, w.cfg.theme)
        self.assertEqual(theme.scheme().id, cs.id)
        theme.apply(self.app, "scheme:gibt-es-nicht")
        self.assertEqual(theme.scheme().id, schemes.SESAM_LIGHT.id if not theme.is_dark() else schemes.SESAM_DARK.id)
        w.close()

    def test_sync_removing_the_active_scheme_falls_back_visually_but_keeps_the_setting(self):
        from sesam.gui import theme

        cs = make()
        storage.save(Config(theme="scheme:" + cs.id, color_schemes=[cs]), self.path)
        w = self._window()
        w.cfg.color_schemes.clear()
        w._sync_finished(None, None, False)
        self.assertNotEqual(theme.scheme().id, cs.id)
        self.assertEqual(w.cfg.theme, "scheme:" + cs.id)
        w.cfg.color_schemes.append(cs)
        w._sync_finished(None, None, False)
        self.assertEqual(theme.scheme().id, cs.id)

    def test_edit_schemes_opens_the_dialog_with_the_active_scheme(self):
        from PySide6.QtWidgets import QDialog

        cs = make()
        storage.save(Config(theme="scheme:" + cs.id, color_schemes=[cs]), self.path)
        w = self._window()
        seen = {}

        def fake_exec(dlg):
            seen["current"] = dlg._current()
            seen["use_after"] = dlg.use_after.isChecked()
            return 0

        with mock.patch.object(QDialog, "exec", fake_exec):
            w.edit_schemes()
        self.assertEqual(seen, {"current": cs.id, "use_after": True})


if __name__ == "__main__":
    unittest.main()
