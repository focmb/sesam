"""Import und Export von Farbschemas."""
import json
import unittest

from sesam import scheme_io, schemes
from sesam.models import ColorScheme


def good() -> dict:
    return json.loads(scheme_io.export_text(ColorScheme.from_scheme(schemes.MONOKAI, "Mein Monokai")))


class SchemeIoTests(unittest.TestCase):
    def test_roundtrip_gets_new_id(self):
        cs = ColorScheme.from_scheme(schemes.DRACULA, "Drache")
        back = scheme_io.parse(scheme_io.export_text(cs))
        self.assertNotEqual(back.id, cs.id)
        self.assertEqual((back.name, back.base, back.ui, back.terminal), (cs.name, cs.base, cs.ui, cs.terminal))

    def test_name_clash_gets_number(self):
        text = scheme_io.export_text(ColorScheme.from_scheme(schemes.NORD, "Nord"))
        self.assertEqual(scheme_io.parse(text, ["nord"]).name, "Nord (2)")
        self.assertEqual(scheme_io.parse(text, ["Nord", "Nord (2)"]).name, "Nord (3)")
        long = ColorScheme.from_scheme(schemes.NORD, "x" * schemes.NAME_MAX)
        self.assertLessEqual(len(scheme_io.parse(scheme_io.export_text(long), ["x" * schemes.NAME_MAX]).name), schemes.NAME_MAX)

    def test_colors_are_lowercased(self):
        d = good()
        d["ui"]["bg"] = "#ABCDEF"
        self.assertEqual(scheme_io.parse(json.dumps(d)).ui["bg"], "#abcdef")

    def test_invalid_files_are_rejected(self):
        def bad(mutate):
            d = good()
            mutate(d)
            with self.assertRaises(scheme_io.SchemeFileError):
                scheme_io.parse(json.dumps(d))

        bad(lambda d: d.update(format="x"))
        bad(lambda d: d.update(version=2))
        bad(lambda d: d.update(name=""))
        bad(lambda d: d.update(name=5))
        bad(lambda d: d.update(base="blau"))
        bad(lambda d: d["ui"].update(bg="rot"))
        bad(lambda d: d["ui"].pop("accent"))
        bad(lambda d: d.update(ui=[]))
        bad(lambda d: d["terminal"].update(cursor="#12"))
        bad(lambda d: d["terminal"].update(ansi=d["terminal"]["ansi"][:15]))
        bad(lambda d: d["terminal"].update(ansi=["#000000"] * 15 + [7]))
        bad(lambda d: d.update(terminal=None))

    def test_garbage_and_size_limit(self):
        for raw in (b"", b"\xff\xfe", b"[1,2]", b"{", b"null"):
            with self.assertRaises(scheme_io.SchemeFileError):
                scheme_io.parse(raw)
        with self.assertRaises(scheme_io.SchemeFileError):
            scheme_io.parse(b" " * (scheme_io.MAX_BYTES + 1))

    def test_bom_is_accepted(self):
        self.assertEqual(scheme_io.parse(b"\xef\xbb\xbf" + scheme_io.export_text(ColorScheme.from_scheme(schemes.NORD)).encode()).name, "Nord")


if __name__ == "__main__":
    unittest.main()
