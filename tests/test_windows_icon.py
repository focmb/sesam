"""Das Windows-Icon (packaging/windows/sesam.ico) ist vorhanden, gültig und wird im Build eingebunden."""
import struct
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ICO = ROOT / "packaging" / "windows" / "sesam.ico"
EXPECTED = (16, 24, 32, 48, 64, 128, 256)


class WindowsIconTests(unittest.TestCase):
    def test_ico_is_valid_and_has_all_sizes(self):
        data = ICO.read_bytes()
        reserved, kind, count = struct.unpack("<HHH", data[:6])
        self.assertEqual((reserved, kind), (0, 1))
        sizes = []
        for i in range(count):
            width, height, _c, _r, _planes, bpp, length, offset = struct.unpack("<BBBBHHII", data[6 + 16 * i:22 + 16 * i])
            sizes.append(width or 256)
            self.assertEqual(width, height)
            self.assertEqual(bpp, 32)
            self.assertEqual(data[offset:offset + 8], b"\x89PNG\r\n\x1a\n")  # PNG-Bild im ICO-Container
            self.assertLessEqual(offset + length, len(data))
        self.assertEqual(tuple(sizes), EXPECTED)

    def test_spec_uses_the_icon_on_windows(self):
        spec = (ROOT / "packaging" / "sesam.spec").read_text(encoding="utf-8")
        self.assertIn("sesam.ico", spec)
        self.assertEqual(spec.count("icon=ICON"), 2)  # sesam.exe und sesam-cli.exe


if __name__ == "__main__":
    unittest.main()
