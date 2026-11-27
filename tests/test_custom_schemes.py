"""Eigene Farbschemas: Modell, Validierung, Speicherung, Register."""
import json
import tempfile
import unittest
from pathlib import Path

from sesam import schemes, storage
from sesam.models import ColorScheme, Config


class ColorSchemeModelTests(unittest.TestCase):
    def tearDown(self):
        schemes.set_custom([])

    def test_from_dict_repairs_invalid_values(self):
        c = ColorScheme.from_dict({"name": "  A   b ", "base": "foo", "ui": {"bg": "#ABCDEF", "text": "rot"},
                                   "terminal": {"ansi": ["#000"]}})
        self.assertEqual(c.name, "A b")
        self.assertEqual(c.base, "dark")
        self.assertEqual(c.ui["bg"], "#abcdef")
        self.assertEqual(c.ui["text"], schemes.SESAM_DARK.ui["text"])
        self.assertEqual(len(c.terminal["ansi"]), schemes.ANSI_COUNT)
        self.assertTrue(c.id)

    def test_name_default_and_limit(self):
        self.assertEqual(ColorScheme.from_dict({}).name, "Farbschema")
        self.assertEqual(len(ColorScheme.from_dict({"name": "x" * 500}).name), schemes.NAME_MAX)

    def test_from_dict_rejects_non_dict(self):
        with self.assertRaises(TypeError):
            ColorScheme.from_dict([])

    def test_copy_gets_new_id_and_roundtrips(self):
        c = ColorScheme.from_scheme(schemes.MONOKAI, "Meins")
        self.assertEqual(c.ui, schemes.MONOKAI.ui)
        s = c.to_scheme()
        self.assertFalse(s.builtin)
        self.assertEqual(s.terminal["ansi"], schemes.MONOKAI.terminal["ansi"])
        self.assertNotEqual(c.id, schemes.MONOKAI.id)

    def test_registry(self):
        c = ColorScheme.from_scheme(schemes.NORD, "Zeta")
        d = ColorScheme.from_scheme(schemes.NORD, "alpha")
        schemes.set_custom([c.to_scheme(), d.to_scheme()])
        self.assertEqual([s.name for s in schemes.custom()], ["alpha", "Zeta"])
        self.assertEqual(schemes.all_schemes()[:len(schemes.BUILTIN)], list(schemes.BUILTIN))
        self.assertEqual(schemes.by_id(c.id).name, "Zeta")
        self.assertEqual(schemes.mode_for(c.to_scheme()), "scheme:" + c.id)
        self.assertEqual(schemes.normalize_mode("scheme:" + c.id), "scheme:" + c.id)

    def test_registry_ignores_builtin_ids(self):
        fake = ColorScheme.from_scheme(schemes.NORD)
        fake.id = schemes.NORD.id
        schemes.set_custom([fake.to_scheme()])
        self.assertEqual(schemes.custom(), [])
        self.assertTrue(schemes.by_id(schemes.NORD.id).builtin)


class StorageTests(unittest.TestCase):
    def test_roundtrip_and_cleanup(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "config.json"
            c = ColorScheme.from_scheme(schemes.DRACULA, "Mein Drachen")
            storage.save(Config(color_schemes=[c]), path)
            self.assertEqual(storage.load(path).color_schemes, [c])
            data = json.loads(path.read_text(encoding="utf-8"))
            data["color_schemes"] = [5, {"id": schemes.NORD.id, "name": "x"}, data["color_schemes"][0],
                                     data["color_schemes"][0], {"name": "ok"}]
            path.write_text(json.dumps(data), encoding="utf-8")
            loaded = storage.load(path).color_schemes
            self.assertEqual([s.name for s in loaded], ["Mein Drachen", "ok"])

    def test_missing_or_wrong_type(self):
        self.assertEqual(storage._color_schemes(None), [])
        self.assertEqual(storage._color_schemes("x"), [])


class SyncTests(unittest.TestCase):
    def setUp(self):
        import test_sync

        self.h = test_sync.EngineTests("test_first_device_pushes_encrypted_and_second_device_receives")
        self.h.setUp()
        self.addCleanup(self.h.tmp.cleanup)

    def _roundtrip(self, passwords_on):
        cfgA, A, _ = self.h.device("A", passwords_on=passwords_on)
        cs = ColorScheme.from_scheme(schemes.NORD, "Mein Nord")
        cfgA.color_schemes.append(cs)
        A.run(cfgA)
        files = self.h.remote_text()
        self.assertIn(f"schemes/{cs.id}.json", files)
        self.assertIn("#2e3440", files[f"schemes/{cs.id}.json"].lower())
        self.assertNotIn("use_password", files[f"schemes/{cs.id}.json"])

        cfgB, B, _ = self.h.device("B", passwords_on=passwords_on)
        B.run(cfgB)
        self.assertEqual(cfgB.color_schemes, [cs])

        cfgB.color_schemes[0].ui["accent"] = "#112233"
        B.run(cfgB)
        A.run(cfgA)
        self.assertEqual(cfgA.color_schemes[0].ui["accent"], "#112233")
        cfgA.color_schemes.clear()
        A.run(cfgA)
        B.run(cfgB)
        self.assertEqual(cfgB.color_schemes, [])
        self.assertFalse([f for f in self.h.remote_text() if f.startswith("schemes/")])

    def test_schemes_sync_with_passwords(self):
        self._roundtrip(True)

    def test_schemes_sync_without_passwords(self):
        self._roundtrip(False)


if __name__ == "__main__":
    unittest.main()
