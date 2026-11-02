"""Fernzugriff: Modell, Viewer-Befehl, Passwortübergabe, Sitzungen (direkt und über SSH-Tunnel)."""
import base64
import os
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))

from sesam import launcher, passwords, storage, tunnels, viewers, remote
from sesam.models import Config, Connection, Viewer

SECRET = bytes(range(32))  # wie der 32-Byte-Schlüssel in remmina.pref


def wait_for(cond, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


class ModelTests(unittest.TestCase):
    def test_roundtrip_in_config_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.json"
            conn = Connection("pc", "10.0.0.5", remote="vnc", remote_mode="direct", remote_port=5901, remote_host="intern", remote_viewer_id="v1", remote_use_password=True)
            viewer = Viewer("Remmina", "remmina -c {profile}")
            storage.save(Config(connections=[conn], viewers=[viewer], default_viewer_id=viewer.id), path)
            loaded = storage.load(path)
        c = loaded.connections[0]
        self.assertEqual((c.remote, c.remote_mode, c.remote_port, c.remote_host, c.remote_viewer_id, c.remote_use_password),
                         ("vnc", "direct", 5901, "intern", "v1", True))
        self.assertEqual((loaded.viewers[0].name, loaded.default_viewer_id), ("Remmina", viewer.id))

    def test_bad_values_never_crash(self):
        c = Connection.from_dict({"name": "a", "host": "h", "remote": "boom", "remote_mode": 5, "remote_port": 99999,
                                     "remote_use_password": "ja", "remote_host": None})
        self.assertEqual((c.remote, c.remote_mode, c.remote_port, c.remote_use_password, c.remote_host), ("", "tunnel", 0, False, ""))
        self.assertEqual(storage._from_data({"viewers": "kaputt"}).viewers, [])
        self.assertEqual(storage._from_data({"viewers": [1, None, {"name": "ok", "command": "x"}]}).viewers[0].name, "ok")

    def test_old_config_without_vnc_gets_default_viewers(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.json"
            path.write_text('{"connections": [{"name": "a", "host": "h"}], "terminals": []}', encoding="utf-8")
            cfg = storage.load(path)
        self.assertEqual(cfg.connections[0].remote, "")
        for protocol in ("vnc", "rdp"):
            self.assertTrue(any(v.protocol == protocol for v in cfg.viewers))
            self.assertEqual(cfg.viewer_by_id(cfg.default_viewer_for(protocol)).protocol, protocol)

    def test_version_3_0_connection_is_migrated(self):
        c = Connection.from_dict({"name": "a", "host": "h", "vnc": "direct", "vnc_port": 5901, "vnc_host": "x", "vnc_viewer_id": "v",
                                  "vnc_use_password": True})
        self.assertEqual((c.remote, c.remote_mode, c.remote_port, c.remote_host, c.remote_viewer_id, c.remote_use_password),
                         ("vnc", "direct", 5901, "x", "v", True))
        off = Connection.from_dict({"name": "a", "host": "h", "vnc": "", "vnc_port": 5900})
        self.assertEqual((off.remote, off.remote_mode), ("", "tunnel"))
        self.assertEqual(Connection.from_dict({"name": "a", "host": "h", "vnc": "tunnel"}).remote_mode, "tunnel")

    def test_viewers_keep_their_protocol_and_bad_protocol_falls_back(self):
        self.assertEqual(Viewer.from_dict({"name": "a", "command": "x", "protocol": "rdp"}).protocol, "rdp")
        self.assertEqual(Viewer.from_dict({"name": "a", "command": "x", "protocol": "boom"}).protocol, "vnc")
        self.assertEqual(Viewer.from_dict({"name": "a", "command": "x"}).protocol, "vnc")

    def test_default_viewers_are_for_this_platform(self):
        names = [v.name for v in viewers.template_viewers()]
        self.assertTrue(names)
        self.assertTrue(all(v.platform in ("all", "linux", "windows") for v in viewers.default_viewers()))


class CommandTests(unittest.TestCase):
    def test_placeholders(self):
        v = Viewer("x", "viewer {host}::{port} --display={display} {url} {title}")
        self.assertEqual(remote.viewer_command(v, "127.0.0.1", 5901, "Mein PC"),
                         ["viewer", "127.0.0.1::5901", "--display=1", "vnc://127.0.0.1:5901", "Mein PC"])

    def test_password_file_argument_and_its_option_are_dropped_without_password(self):
        v = Viewer("x", "vncviewer -passwd {password_file} {host}::{port}")
        self.assertEqual(remote.viewer_command(v, "h", 5900, "t"), ["vncviewer", "h::5900"])
        self.assertEqual(remote.viewer_command(v, "h", 5900, "t", password_file="/tmp/p"), ["vncviewer", "-passwd", "/tmp/p", "h::5900"])
        joined = Viewer("x", "vncviewer -PasswordFile={password_file} {host}::{port}")
        self.assertEqual(remote.viewer_command(joined, "h", 5900, "t"), ["vncviewer", "h::5900"])

    def test_empty_template_is_an_error(self):
        with self.assertRaises(launcher.LaunchError):
            remote.viewer_command(Viewer("x", "   "), "h", 5900, "t")

    def test_hostile_target_is_refused(self):
        conn = Connection("a", "-oProxyCommand=x", remote="vnc", remote_mode="direct")
        with self.assertRaises(launcher.LaunchError):
            remote.check_target(conn, conn.host)
        with self.assertRaises(launcher.LaunchError):
            remote.check_target(Connection("a", "h x", remote="vnc", remote_mode="direct"), "h x")

    def test_resolve_viewer_prefers_connection_then_default(self):
        a, b = Viewer("a", "a"), Viewer("b", "b")
        cfg = Config(viewers=[a, b], default_viewer_id=b.id)
        self.assertIs(remote.resolve_viewer(cfg, Connection("c", "h", remote="vnc")), b)
        self.assertIs(remote.resolve_viewer(cfg, Connection("c", "h", remote="vnc", remote_viewer_id=a.id)), a)
        self.assertIs(remote.resolve_viewer(cfg, Connection("c", "h", remote="vnc", remote_viewer_id="weg")), b)
        with self.assertRaises(launcher.LaunchError):
            remote.resolve_viewer(Config(), Connection("c", "h", remote="vnc"))

    def test_resolve_viewer_only_takes_viewers_of_the_protocol(self):
        v, r = Viewer("vnc", "v"), Viewer("rdp", "r", protocol="rdp")
        cfg = Config(viewers=[v, r], default_viewer_id=v.id, default_rdp_viewer_id=r.id)
        self.assertIs(remote.resolve_viewer(cfg, Connection("c", "h", remote="rdp")), r)
        self.assertIs(remote.resolve_viewer(cfg, Connection("c", "h", remote="rdp", remote_viewer_id=v.id)), r)  # VNC-Viewer passt nicht
        self.assertIs(remote.resolve_viewer(cfg, Connection("c", "h", remote="vnc", remote_viewer_id=r.id)), v)
        with self.assertRaises(launcher.LaunchError):
            remote.resolve_viewer(Config(viewers=[v]), Connection("c", "h", remote="rdp"))

    def test_rdp_command_drops_empty_user_and_domain_and_appends_extra(self):
        v = Viewer("x", "xfreerdp /v:{host}:{port} /u:{user} /d:{domain} /from-stdin", protocol="rdp")
        self.assertEqual(remote.viewer_command(v, "h", 3389, "t", user="ich", domain="CORP", extra="/f +clipboard"),
                         ["xfreerdp", "/v:h:3389", "/u:ich", "/d:CORP", "/from-stdin", "/f", "+clipboard"])
        self.assertEqual(remote.viewer_command(v, "h", 3389, "t"), ["xfreerdp", "/v:h:3389", "/from-stdin"])
        self.assertEqual(remote.viewer_command(Viewer("x", "krdc {url}", protocol="rdp"), "h", 3389, "t"), ["krdc", "rdp://h:3389"])
        self.assertTrue(remote.uses_stdin_password(v))
        self.assertTrue(remote.uses_stdin_password(Viewer("x", "xfreerdp /from-stdin:force", protocol="rdp")))
        self.assertFalse(remote.uses_stdin_password(Viewer("x", "xfreerdp /p:{password_file}", protocol="rdp")))

    def test_rdp_file_has_no_password_and_no_line_injection(self):
        text = remote.rdp_file_text("10.0.0.1", 3390, "ich\nfull address:s:evil", "CORP")
        self.assertEqual(len(text.splitlines()), 2)  # kein Zeilenumbruch eingeschleust
        self.assertEqual(text.splitlines()[0], "full address:s:10.0.0.1:3390")
        self.assertIn("username:s:CORP\\ich full address:s:evil", text)
        self.assertNotIn("password", text.lower())
        self.assertEqual(remote.rdp_file_text("h", 3389), "full address:s:h:3389\r\n")

    def test_rdp_remmina_profile(self):
        text = remote.remmina_profile("PC", "h", 3389, "geheim", SECRET, "rdp", "ich", "CORP")
        lines = text.splitlines()
        self.assertEqual(lines[:6], ["[remmina]", "name=PC", "protocol=RDP", "server=h:3389", "username=ich", "domain=CORP"])
        self.assertEqual(remote.remmina_decrypt(lines[6].split("=", 1)[1], SECRET), "geheim")
        self.assertNotIn("username=", remote.remmina_profile("PC", "h", 5900, None, None, "vnc", "ich", "CORP"))

    def test_port_defaults_per_protocol(self):
        self.assertEqual(remote.port_of(Connection("c", "h", remote="vnc")), 5900)
        self.assertEqual(remote.port_of(Connection("c", "h", remote="rdp")), 3389)
        self.assertEqual(remote.port_of(Connection("c", "h", remote="rdp", remote_port=3390)), 3390)
        f = remote.tunnel_forward(Connection("c", "srv", remote="rdp"), 40002)
        self.assertEqual((tunnels.forward_spec(f), f.name), (["-L", "127.0.0.1:40002:localhost:3389"], "RDP"))

    def test_passwords_are_kept_per_protocol(self):
        self.assertNotEqual(remote.password_key("id", "vnc"), remote.password_key("id", "rdp"))
        stored = {}
        conn = Connection("c", "h", remote="rdp")
        with mock.patch.object(passwords, "set_password", lambda i, p: stored.__setitem__(i, p)), \
                mock.patch.object(passwords, "delete_password", lambda i: stored.pop(i, None)):
            remote.apply_password_change(conn, "pw")
            self.assertEqual(stored, {f"{conn.id}:rdp": "pw"})
            self.assertTrue(conn.remote_use_password)
            stored[f"{conn.id}:vnc"] = "alt"
            remote.forget_passwords(conn.id)
            self.assertEqual(stored, {})
            remote.apply_password_change(conn, "")
            self.assertFalse(conn.remote_use_password)
            remote.apply_password_change(Connection("c", "h"), "pw")  # Fernzugriff aus: nichts speichern
            self.assertEqual(stored, {})

    def test_tunnel_forward_uses_loopback_and_target(self):
        f = remote.tunnel_forward(Connection("c", "srv", remote="vnc", remote_mode="tunnel", remote_port=5902, remote_host="gerät"), 40000)
        self.assertEqual(tunnels.forward_spec(f), ["-L", "127.0.0.1:40000:gerät:5902"])
        f = remote.tunnel_forward(Connection("c", "srv", remote="vnc", remote_mode="tunnel"), 40001)
        self.assertEqual(tunnels.forward_spec(f), ["-L", "127.0.0.1:40001:localhost:5900"])

    def test_free_port_is_usable(self):
        import socket

        port = remote.free_port()
        with socket.socket() as s:
            s.bind(("127.0.0.1", port))


class PasswordTests(unittest.TestCase):
    def test_vnc_password_file_format(self):
        data = remote.obfuscate_password("geheim")
        self.assertEqual(len(data), 8)
        self.assertNotEqual(data, b"geheim\0\0")
        self.assertEqual(remote.obfuscate_password("geheim"), data)  # deterministisch
        self.assertEqual(remote.obfuscate_password("Secure!").hex(), "d7a514d8c556aade")  # bekannter Testvektor von vncpasswd
        self.assertEqual(remote.obfuscate_password("12345678extra"), remote.obfuscate_password("12345678"))  # VNC kennt nur 8 Zeichen

    def test_remmina_roundtrip(self):
        for pw in ("a", "geheim", "12345678", "längeres Passwort mit Ümlauten"):
            enc = remote.remmina_encrypt(pw, SECRET)
            base64.b64decode(enc)
            self.assertEqual(remote.remmina_decrypt(enc, SECRET), pw)
        self.assertEqual(len(base64.b64decode(remote.remmina_encrypt("12345678", SECRET))), 16)  # immer mindestens ein Auffüllbyte

    def test_remmina_secret_from_pref_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            pref = Path(tmp) / "remmina" / "remmina.pref"
            pref.parent.mkdir()
            pref.write_text("[remmina_pref]\nfoo=bar\nsecret=" + base64.b64encode(SECRET).decode() + "\n", encoding="utf-8")
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp}), mock.patch.object(Path, "home", return_value=Path(tmp) / "home"):
                self.assertEqual(remote.remmina_secret(), SECRET)
            pref.write_text("[remmina_pref]\nsecret=kein-base64!\n", encoding="utf-8")
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp}), mock.patch.object(Path, "home", return_value=Path(tmp) / "home"):
                self.assertIsNone(remote.remmina_secret())

    def test_remmina_profile(self):
        text = remote.remmina_profile("PC\nprotocol=RDP", "127.0.0.1", 40000, "geheim", SECRET)
        lines = text.splitlines()
        self.assertEqual(lines[:4], ["[remmina]", "name=PC protocol=RDP", "protocol=VNC", "server=127.0.0.1:40000"])  # keine Zeileneinschleusung
        self.assertEqual(remote.remmina_decrypt(lines[4].split("=", 1)[1], SECRET), "geheim")
        self.assertNotIn("password=", remote.remmina_profile("PC", "h", 5900, "geheim", None))
        self.assertNotIn("password=", remote.remmina_profile("PC", "h", 5900, None, SECRET))


def fake_viewer(tmp: str, body: str) -> Viewer:
    """Viewer, der ein Python-Skript startet; ``body`` sieht ``sys.argv[1:]`` und ``OUT`` (Datei für Aufzeichnungen)."""
    script = Path(tmp) / "viewer.py"
    out = Path(tmp) / "out.txt"
    script.write_text("import sys, time\nOUT = %r\n" % str(out) + textwrap.dedent(body), encoding="utf-8")
    return Viewer("fake", f'"{sys.executable}" "{script}" {{host}} {{port}}', platform="all")


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tunnels = tunnels.Manager()
        self.mgr = remote.Manager(self.tunnels)
        self.addCleanup(self.tunnels.stop_all)
        self.addCleanup(self.mgr.stop_all)

    def _cfg(self, viewer, conn):
        return Config(connections=[conn], viewers=[viewer], default_viewer_id=viewer.id)

    def _out(self):
        return Path(self.tmp.name, "out.txt").read_text(encoding="utf-8")

    def test_direct_starts_viewer_on_host_and_stops_it(self):
        viewer = fake_viewer(self.tmp.name, "open(OUT, 'w', encoding='utf-8', newline='').write(' '.join(sys.argv[1:]))\ntime.sleep(60)\n")
        conn = Connection("Gerät", "10.1.2.3", remote="vnc", remote_mode="direct", remote_port=5901)
        cfg = self._cfg(viewer, conn)
        self.assertEqual(self.mgr.begin(cfg, conn, None), [])
        self.assertTrue(self.mgr.is_open(conn.id))
        self.assertTrue(wait_for(lambda: Path(self.tmp.name, "out.txt").exists() and self._out() == "10.1.2.3 5901"))
        self.assertEqual(self.mgr.poll(cfg), [])
        proc = self.mgr.sessions[conn.id].proc
        self.mgr.stop(conn.id)
        self.assertFalse(self.mgr.is_open(conn.id))
        self.assertTrue(wait_for(lambda: proc.poll() is not None))

    def test_direct_session_ends_when_viewer_ends(self):
        viewer = fake_viewer(self.tmp.name, "pass\n")
        conn = Connection("Gerät", "10.1.2.3", remote="vnc", remote_mode="direct")
        cfg = self._cfg(viewer, conn)
        self.mgr.begin(cfg, conn, None)
        self.assertTrue(wait_for(lambda: self.mgr.sessions[conn.id].proc.poll() is not None))
        self.mgr.poll(cfg)
        self.assertFalse(self.mgr.is_open(conn.id))

    def test_second_start_does_not_open_a_second_viewer(self):
        viewer = fake_viewer(self.tmp.name, "time.sleep(60)\n")
        conn = Connection("Gerät", "10.1.2.3", remote="vnc", remote_mode="direct")
        cfg = self._cfg(viewer, conn)
        self.mgr.begin(cfg, conn, None)
        first = self.mgr.sessions[conn.id].proc
        events = self.mgr.begin(cfg, conn, None)
        self.assertIs(self.mgr.sessions[conn.id].proc, first)
        self.assertTrue(events and not events[0][1])

    def test_off_and_missing_viewer_are_errors(self):
        conn = Connection("a", "h")
        with self.assertRaises(launcher.LaunchError):
            self.mgr.begin(Config(connections=[conn]), conn, None)
        conn.remote, conn.remote_mode = "vnc", "direct"
        with self.assertRaises(launcher.LaunchError):
            self.mgr.begin(Config(connections=[conn]), conn, None)  # kein Viewer konfiguriert
        self.assertFalse(self.mgr.is_open(conn.id))

    def test_unknown_program_is_a_readable_error(self):
        conn = Connection("a", "h", remote="vnc", remote_mode="direct")
        cfg = self._cfg(Viewer("x", "definitely-not-a-viewer-xyz {host}"), conn)
        with self.assertRaises(launcher.LaunchError):
            self.mgr.begin(cfg, conn, None)
        self.assertFalse(self.mgr.is_open(conn.id))

    def test_password_goes_through_a_private_file_and_is_removed(self):
        body = """
            import os
            path = sys.argv[1]  # {host}
            open(OUT, 'w', encoding='utf-8', newline='').write(path)
            time.sleep(60)
        """
        script = Path(self.tmp.name) / "viewer.py"
        script.write_text(textwrap.dedent("""
            import sys, time
            data = open(sys.argv[1], 'rb').read()
            open(%r, 'w', encoding='utf-8', newline='').write(data.hex())
            time.sleep(60)
        """ % str(Path(self.tmp.name) / "out.txt")), encoding="utf-8")
        viewer = Viewer("fake", f'"{sys.executable}" "{script}" {{password_file}}')
        conn = Connection("Gerät", "10.1.2.3", remote="vnc", remote_mode="direct", remote_use_password=True)
        cfg = self._cfg(viewer, conn)
        with mock.patch.object(passwords, "get_password", return_value="geheim") as get:
            self.mgr.begin(cfg, conn, None)
        get.assert_called_once_with(f"{conn.id}:vnc")
        self.assertTrue(wait_for(lambda: Path(self.tmp.name, "out.txt").exists() and self._out()))
        self.assertEqual(self._out(), remote.obfuscate_password("geheim").hex())
        tmpdir = self.mgr.sessions[conn.id].tmpdir
        self.assertTrue(os.path.isdir(tmpdir))
        if os.name == "posix":
            self.assertEqual(os.stat(tmpdir).st_mode & 0o077, 0)
        self.mgr.stop(conn.id)
        self.assertFalse(os.path.exists(tmpdir))

    def test_password_file_is_removed_after_a_while(self):
        script = Path(self.tmp.name) / "viewer.py"
        script.write_text("import sys, time\ntime.sleep(60)\n", encoding="utf-8")
        viewer = Viewer("fake", f'"{sys.executable}" "{script}" -passwd {{password_file}}')
        conn = Connection("Gerät", "10.1.2.3", remote="vnc", remote_mode="direct", remote_use_password=True)
        cfg = self._cfg(viewer, conn)
        with mock.patch.object(passwords, "get_password", return_value="geheim"):
            self.mgr.begin(cfg, conn, None)
        sess = self.mgr.sessions[conn.id]
        path = sess.password_file
        self.assertTrue(os.path.exists(path))
        with mock.patch.object(remote, "PASSWORD_FILE_TTL", -1.0):
            self.mgr.poll(cfg)
        self.assertFalse(os.path.exists(path))
        self.assertTrue(self.mgr.is_open(conn.id))

    def test_missing_password_still_starts_viewer(self):
        script = Path(self.tmp.name) / "viewer.py"
        script.write_text("import sys, time\nopen(%r, 'w', encoding='utf-8', newline='').write(' '.join(sys.argv[1:]))\ntime.sleep(60)\n" % str(Path(self.tmp.name) / "out.txt"), encoding="utf-8")
        viewer = Viewer("fake", f'"{sys.executable}" "{script}" -passwd {{password_file}} {{host}}')
        conn = Connection("Gerät", "10.1.2.3", remote="vnc", remote_mode="direct", remote_use_password=True)
        cfg = self._cfg(viewer, conn)
        with mock.patch.object(passwords, "get_password", side_effect=passwords.PasswordError("kein Schlüsselbund")):
            events = self.mgr.begin(cfg, conn, None)
        self.assertTrue(events and events[0][1])  # Warnung statt Abbruch
        self.assertTrue(wait_for(lambda: Path(self.tmp.name, "out.txt").exists() and self._out() == "10.1.2.3"))

    def test_remmina_profile_is_written_with_encrypted_password(self):
        script = Path(self.tmp.name) / "viewer.py"
        script.write_text("import sys, time\nopen(%r, 'w', encoding='utf-8', newline='').write(open(sys.argv[1], encoding='utf-8').read())\ntime.sleep(60)\n"
                          % str(Path(self.tmp.name) / "out.txt"), encoding="utf-8")
        viewer = Viewer("fake", f'"{sys.executable}" "{script}" {{profile}}')
        conn = Connection("Gerät", "10.1.2.3", remote="vnc", remote_mode="direct", remote_port=5902, remote_use_password=True)
        cfg = self._cfg(viewer, conn)
        with mock.patch.object(passwords, "get_password", return_value="geheim"), mock.patch.object(remote, "remmina_secret", return_value=SECRET):
            self.assertEqual(self.mgr.begin(cfg, conn, None), [])
        self.assertTrue(wait_for(lambda: Path(self.tmp.name, "out.txt").exists() and self._out()))
        text = self._out()
        self.assertIn("protocol=VNC", text)
        self.assertIn("server=10.1.2.3:5902", text)
        enc = next(line.split("=", 1)[1] for line in text.splitlines() if line.startswith("password="))
        self.assertEqual(remote.remmina_decrypt(enc, SECRET), "geheim")

    def test_remmina_without_secret_warns_and_omits_password(self):
        script = Path(self.tmp.name) / "viewer.py"
        script.write_text("import sys, time\nopen(%r, 'w', encoding='utf-8', newline='').write(open(sys.argv[1], encoding='utf-8').read())\ntime.sleep(60)\n"
                          % str(Path(self.tmp.name) / "out.txt"), encoding="utf-8")
        viewer = Viewer("fake", f'"{sys.executable}" "{script}" {{profile}}')
        conn = Connection("Gerät", "10.1.2.3", remote="vnc", remote_mode="direct", remote_use_password=True)
        cfg = self._cfg(viewer, conn)
        with mock.patch.object(passwords, "get_password", return_value="geheim"), mock.patch.object(remote, "remmina_secret", return_value=None):
            events = self.mgr.begin(cfg, conn, None)
        self.assertTrue(events and events[0][1])
        self.assertTrue(wait_for(lambda: Path(self.tmp.name, "out.txt").exists() and self._out()))
        self.assertNotIn("password=", self._out())

    # --- RDP ---
    def _rdp_viewer(self, body_args: str) -> Viewer:
        script = Path(self.tmp.name) / "viewer.py"
        return Viewer("fake", f'"{sys.executable}" "{script}" {body_args}', protocol="rdp")

    def test_rdp_password_goes_through_stdin_not_arguments(self):
        script = Path(self.tmp.name) / "viewer.py"
        script.write_text("import sys, time\nline = sys.stdin.readline()\nopen(%r, 'w', encoding='utf-8', newline='').write(line.rstrip('\\n') + '|' + ' '.join(sys.argv[1:]))\n"
                          "time.sleep(60)\n" % str(Path(self.tmp.name) / "out.txt"), encoding="utf-8")
        viewer = self._rdp_viewer("/v:{host}:{port} /u:{user} /d:{domain} /from-stdin")
        conn = Connection("PC", "10.1.2.3", remote="rdp", remote_mode="direct", remote_user="ich", remote_domain="CORP",
                          remote_args="/f", remote_use_password=True)
        cfg = Config(connections=[conn], viewers=[viewer], default_rdp_viewer_id=viewer.id)
        with mock.patch.object(passwords, "get_password", return_value="geheim") as get:
            self.assertEqual(self.mgr.begin(cfg, conn, None), [])
        get.assert_called_once_with(f"{conn.id}:rdp")
        self.assertTrue(wait_for(lambda: Path(self.tmp.name, "out.txt").exists() and self._out()))
        password, args = self._out().split("|")
        self.assertEqual(password, "geheim")
        self.assertEqual(args, "/v:10.1.2.3:3389 /u:ich /d:CORP /from-stdin /f")
        self.assertNotIn("geheim", args)

    def test_rdp_file_is_written_and_removed_and_mstsc_style_viewer_gets_a_notice(self):
        script = Path(self.tmp.name) / "viewer.py"
        script.write_text("import sys, time\nopen(%r, 'w', encoding='utf-8', newline='').write(open(sys.argv[1], encoding='utf-8', newline='').read())\n"
                          "time.sleep(60)\n" % str(Path(self.tmp.name) / "out.txt"), encoding="utf-8")
        viewer = self._rdp_viewer("{rdp_file}")
        conn = Connection("PC", "10.1.2.3", remote="rdp", remote_mode="direct", remote_user="ich", remote_domain="CORP", remote_use_password=True)
        cfg = Config(connections=[conn], viewers=[viewer], default_rdp_viewer_id=viewer.id)
        with mock.patch.object(passwords, "get_password", return_value="geheim"):
            events = self.mgr.begin(cfg, conn, None)
        self.assertEqual(len(events), 1)
        self.assertFalse(events[0][1])  # Hinweis, kein Fehler: dieser Viewer fragt nach dem Passwort
        self.assertTrue(wait_for(lambda: Path(self.tmp.name, "out.txt").exists() and self._out()))
        self.assertEqual(self._out(), "full address:s:10.1.2.3:3389\nusername:s:CORP\\ich\n")  # gelesen mit universellen Zeilenenden
        tmpdir = self.mgr.sessions[conn.id].tmpdir
        self.assertTrue(os.path.isdir(tmpdir))
        self.mgr.stop(conn.id)
        self.assertFalse(os.path.exists(tmpdir))

    def test_rdp_remmina_profile_has_user_and_encrypted_password(self):
        script = Path(self.tmp.name) / "viewer.py"
        script.write_text("import sys, time\nopen(%r, 'w', encoding='utf-8', newline='').write(open(sys.argv[1], encoding='utf-8').read())\ntime.sleep(60)\n"
                          % str(Path(self.tmp.name) / "out.txt"), encoding="utf-8")
        viewer = self._rdp_viewer("{profile}")
        conn = Connection("PC", "10.1.2.3", remote="rdp", remote_mode="direct", remote_user="ich", remote_use_password=True)
        cfg = Config(connections=[conn], viewers=[viewer], default_rdp_viewer_id=viewer.id)
        with mock.patch.object(passwords, "get_password", return_value="geheim"), mock.patch.object(remote, "remmina_secret", return_value=SECRET):
            self.assertEqual(self.mgr.begin(cfg, conn, None), [])
        self.assertTrue(wait_for(lambda: Path(self.tmp.name, "out.txt").exists() and self._out()))
        text = self._out()
        self.assertIn("protocol=RDP", text)
        self.assertIn("server=10.1.2.3:3389", text)
        self.assertIn("username=ich", text)

    def test_rdp_connection_does_not_use_a_vnc_viewer(self):
        viewer = fake_viewer(self.tmp.name, "time.sleep(60)\n")  # VNC-Viewer
        conn = Connection("PC", "10.1.2.3", remote="rdp", remote_mode="direct")
        with self.assertRaises(launcher.LaunchError):
            self.mgr.begin(Config(connections=[conn], viewers=[viewer], default_viewer_id=viewer.id), conn, None)

    # --- über SSH-Tunnel ---
    def _fake_tunnel(self, body):
        """``start_tunnel`` mit einem Python-Skript statt ssh; merkt sich die Weiterleitung."""
        script = Path(self.tmp.name) / "fake_ssh.py"
        script.write_text(textwrap.dedent(body), encoding="utf-8")
        seen = []

        def start(conn, forward):
            seen.append(forward)
            t = tunnels.Tunnel(conn.id, forward, [sys.executable, str(script)], dict(os.environ), ephemeral=True)
            self.tunnels._tunnels[t.key] = t
            t.start()
            return True

        return start, seen

    READY = """
        import sys, time
        sys.stderr.write("debug1: Entering interactive session.\\n"); sys.stderr.flush()
        time.sleep(60)
    """

    def test_tunnel_mode_starts_viewer_on_the_local_port(self):
        viewer = fake_viewer(self.tmp.name, "open(OUT, 'w', encoding='utf-8', newline='').write(' '.join(sys.argv[1:]))\ntime.sleep(60)\n")
        conn = Connection("Server", "srv", remote="vnc", remote_mode="tunnel", remote_port=5901, remote_host="gerät")
        cfg = self._cfg(viewer, conn)
        start, seen = self._fake_tunnel(self.READY)
        events = self.mgr.begin(cfg, conn, start)
        self.assertEqual(len(events), 1)
        self.assertEqual(self.mgr.sessions[conn.id].state, remote.STARTING)
        self.assertEqual(seen[0].dest_host, "gerät")
        self.assertTrue(wait_for(lambda: (self.mgr.poll(cfg), self.mgr.sessions[conn.id].state == remote.VIEWING)[1]))
        port = seen[0].listen_port
        self.assertTrue(wait_for(lambda: Path(self.tmp.name, "out.txt").exists() and self._out() == f"127.0.0.1 {port}"))
        self.assertEqual(self.tunnels.active(), [])  # kurzlebige Tunnel zählen nicht als Weiterleitung
        self.tunnels.forget_missing(cfg)  # und werden nicht als „nicht mehr konfiguriert“ beendet
        self.assertEqual(self.tunnels.state(conn.id, seen[0].id), tunnels.RUNNING)
        self.mgr.stop(conn.id)
        self.assertEqual(self.tunnels.state(conn.id, seen[0].id), tunnels.STOPPED)

    def test_tunnel_ends_with_the_viewer(self):
        viewer = fake_viewer(self.tmp.name, "time.sleep(1.2)\nsys.exit(3)\n")  # Fehlercode: kein „übergeben“
        conn = Connection("Server", "srv", remote="vnc", remote_mode="tunnel")
        cfg = self._cfg(viewer, conn)
        start, seen = self._fake_tunnel(self.READY)
        self.mgr.begin(cfg, conn, start)
        self.assertTrue(wait_for(lambda: (self.mgr.poll(cfg), not self.mgr.is_open(conn.id))[1], 15))
        self.assertEqual(self.tunnels.state(conn.id, seen[0].id), tunnels.STOPPED)

    def test_viewer_handing_over_keeps_the_tunnel_until_stopped(self):
        viewer = fake_viewer(self.tmp.name, "pass\n")  # beendet sich sofort mit 0, wie Remmina bei laufender Instanz
        conn = Connection("Server", "srv", remote="vnc", remote_mode="tunnel")
        cfg = self._cfg(viewer, conn)
        start, seen = self._fake_tunnel(self.READY)
        self.mgr.begin(cfg, conn, start)
        self.assertTrue(wait_for(lambda: (self.mgr.poll(cfg), self.mgr.sessions[conn.id].state == remote.DETACHED)[1], 15))
        self.assertTrue(self.mgr.is_open(conn.id))
        self.assertEqual(self.tunnels.state(conn.id, seen[0].id), tunnels.RUNNING)
        self.mgr.stop(conn.id)
        self.assertEqual(self.tunnels.state(conn.id, seen[0].id), tunnels.STOPPED)

    def test_failing_tunnel_reports_error_and_closes(self):
        viewer = fake_viewer(self.tmp.name, "time.sleep(60)\n")
        conn = Connection("Server", "srv", remote="vnc", remote_mode="tunnel")
        cfg = self._cfg(viewer, conn)
        start, _seen = self._fake_tunnel("""
            import sys
            sys.stderr.write("user@srv: Permission denied (publickey).\\n")
            sys.exit(255)
        """)
        self.mgr.begin(cfg, conn, start)
        events = []
        self.assertTrue(wait_for(lambda: (events.extend(self.mgr.poll(cfg)), not self.mgr.is_open(conn.id))[1]))
        self.assertTrue(any(err and "Permission denied" in text for text, err in events))
        self.assertEqual(self.tunnels.newly_failed(), [])  # gemeldet hat es die VNC-Sitzung, nicht die Tunnelanzeige

    def test_tunnel_timeout_closes_session(self):
        viewer = fake_viewer(self.tmp.name, "time.sleep(60)\n")
        conn = Connection("Server", "srv", remote="vnc", remote_mode="tunnel")
        cfg = self._cfg(viewer, conn)
        start, seen = self._fake_tunnel("import time; time.sleep(60)")  # meldet nie „bereit“
        self.mgr.begin(cfg, conn, start)
        with mock.patch.object(remote, "START_TIMEOUT", -1.0):
            events = self.mgr.poll(cfg)
        self.assertTrue(events and events[0][1])
        self.assertFalse(self.mgr.is_open(conn.id))
        self.assertEqual(self.tunnels.state(conn.id, seen[0].id), tunnels.STOPPED)

    def test_cancelled_tunnel_start_leaves_nothing_behind(self):
        viewer = fake_viewer(self.tmp.name, "time.sleep(60)\n")
        conn = Connection("Server", "srv", remote="vnc", remote_mode="tunnel")
        self.assertEqual(self.mgr.begin(self._cfg(viewer, conn), conn, lambda c, f: False), [])
        self.assertFalse(self.mgr.is_open(conn.id))

    def test_deleted_connection_closes_session(self):
        viewer = fake_viewer(self.tmp.name, "time.sleep(60)\n")
        conn = Connection("Gerät", "10.1.2.3", remote="vnc", remote_mode="direct")
        cfg = self._cfg(viewer, conn)
        self.mgr.begin(cfg, conn, None)
        proc = self.mgr.sessions[conn.id].proc
        cfg.connections.clear()
        self.mgr.poll(cfg)
        self.assertFalse(self.mgr.is_open(conn.id))
        self.assertTrue(wait_for(lambda: proc.poll() is not None))


if __name__ == "__main__":
    unittest.main()
