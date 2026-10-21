"""Debian-Paket: Aufbau und Metadaten (braucht dpkg-deb, sonst übersprungen)."""
import importlib.util
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("build_deb", ROOT / "packaging" / "build_deb.py")
build_deb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build_deb)


@unittest.skipIf(shutil.which("dpkg-deb") is None, "dpkg-deb nicht verfügbar")
class DebTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.folder = self.tmp / "sesam"
        (self.folder / "_internal" / "lib").mkdir(parents=True)
        (self.folder / "sesam").write_text("#!/bin/sh\n")
        (self.folder / "sesam").chmod(0o700)  # umask/Quelle dürfen das Ergebnis nicht bestimmen
        (self.folder / "_internal" / "data.txt").write_text("x")
        (self.folder / "_internal" / "data.txt").chmod(0o600)
        (self.folder / "_internal" / "lib" / "libx.so.1").write_bytes(b"\x7fELF")
        (self.folder / "_internal" / "lib" / "libx.so").symlink_to("libx.so.1")

    def deb(self, **kw):
        return build_deb.build(self.folder, self.tmp / "out", **kw)

    def field(self, deb, name):
        return subprocess.run(["dpkg-deb", "-f", str(deb), name], capture_output=True, text=True, check=True).stdout.strip()

    def listing(self, deb):
        out = subprocess.run(["dpkg-deb", "-c", str(deb)], capture_output=True, text=True, check=True).stdout
        return {line.split()[5].lstrip("."): line for line in out.splitlines()}

    def test_file_name_and_metadata(self):
        deb = self.deb(version="1.2.3", arch="amd64")
        self.assertEqual(deb.name, "sesam_1.2.3_amd64.deb")
        self.assertEqual(self.field(deb, "Package"), "sesam")
        self.assertEqual(self.field(deb, "Version"), "1.2.3")
        self.assertEqual(self.field(deb, "Architecture"), "amd64")
        depends = self.field(deb, "Depends")
        for needed in ("libxcb-cursor0", "libegl1", "openssh-client", "git"):
            self.assertIn(needed, depends)
        self.assertGreater(int(self.field(deb, "Installed-Size")), 0)

    def test_layout_links_and_modes(self):
        files = self.listing(self.deb(version="1.0", arch="amd64"))
        self.assertTrue(files["/opt/sesam/sesam"].startswith("-rwxr-xr-x root/root"))
        self.assertTrue(files["/opt/sesam/_internal/data.txt"].startswith("-rw-r--r-- root/root"))
        self.assertTrue(files["/usr/bin/sesam"].startswith("lrwxrwxrwx"))
        self.assertIn("-> /opt/sesam/sesam", files["/usr/bin/sesam"])
        self.assertIn("-> libx.so.1", files["/opt/sesam/_internal/lib/libx.so"])  # Verknüpfungen bleiben Verknüpfungen
        self.assertIn("/usr/share/applications/sesam.desktop", files)
        self.assertIn("/usr/share/icons/hicolor/scalable/apps/sesam.svg", files)

    def test_desktop_file_runs_installed_command(self):
        deb = self.deb(version="1.0", arch="amd64")
        out = self.tmp / "x"
        subprocess.run(["dpkg-deb", "-x", str(deb), str(out)], check=True)
        text = (out / "usr/share/applications/sesam.desktop").read_text(encoding="utf-8")
        self.assertIn("\nExec=sesam\n", text)
        self.assertNotIn("@EXEC@", text)

    def test_missing_program_gives_clear_error(self):
        shutil.rmtree(self.folder)
        with self.assertRaises(SystemExit) as ctx:
            self.deb(version="1.0", arch="amd64")
        self.assertIn("packaging/build.py", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
