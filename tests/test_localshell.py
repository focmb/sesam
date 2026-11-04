import os
import unittest
from unittest import mock

from sesam import localshell, runtime


class LocalShellTests(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "POSIX-Pfade")
    def test_uses_shell_variable_on_linux(self):
        with mock.patch.object(runtime, "IS_WINDOWS", False), mock.patch.dict(os.environ, {"SHELL": "/bin/sh"}):
            self.assertEqual(localshell.shell(), ["/bin/sh"])
            args = localshell.command()
        self.assertEqual(args[:2], ["/bin/sh", "-c"])
        self.assertIn('cd "$HOME"', args[2])
        self.assertEqual(args[-1], "/bin/sh")  # $0 des Wrappers: die Shell wird mit exec gestartet

    @unittest.skipIf(os.name == "nt", "POSIX-Pfade")
    def test_falls_back_when_shell_variable_is_missing_or_invalid(self):
        with mock.patch.object(runtime, "IS_WINDOWS", False), mock.patch.dict(os.environ, {"SHELL": "nicht/absolut"}):
            self.assertTrue(os.path.isabs(localshell.shell()[0]))

    def test_windows_prefers_powershell_then_comspec(self):
        with mock.patch.object(runtime, "IS_WINDOWS", True):
            with mock.patch("shutil.which", side_effect=lambda n: r"C:\ps\powershell.exe" if n == "powershell.exe" else None):
                self.assertEqual(localshell.command(), [r"C:\ps\powershell.exe", "-NoLogo"])
            with mock.patch("shutil.which", return_value=None), mock.patch.dict(os.environ, {"COMSPEC": r"C:\cmd.exe"}):
                self.assertEqual(localshell.command(), [r"C:\cmd.exe"])

    @unittest.skipIf(os.name == "nt", "POSIX")
    def test_wrapper_starts_shell_in_home_directory(self):
        import subprocess
        with mock.patch.dict(os.environ, {"SHELL": "/bin/sh"}):
            args = localshell.command()
        args = args[:-1] + ["/bin/sh"]
        out = subprocess.run([*args[:3], "/bin/sh"], input="pwd\n", capture_output=True, text=True, cwd="/", env={**os.environ, "PS1": ""}).stdout
        self.assertIn(os.path.expanduser("~"), out)


if __name__ == "__main__":
    unittest.main()
