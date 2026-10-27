import subprocess
import unittest
from pathlib import Path

from gui_base import GuiTestCase


class SyncConflictGuiTests(GuiTestCase):
    def _item(self, host, pw=None):
        from sesam.syncing import Item

        return Item("c", "id1", {"id": "id1", "name": "server", "host": host, "port": 22}, pw)

    def _conflicts(self):
        from sesam.syncing import Conflict

        return [Conflict(("c", "id1"), self._item("lokal.example"), self._item("remote.example"), "tok1"),
                Conflict(("c", "id2"), self._item("a", "x"), self._item("a", "y"), "tok2")]

    def test_dialog_defaults_to_local_and_offers_remote(self):
        from sesam.gui.sync_conflict_dialog import SyncConflictDialog, describe

        conflicts = self._conflicts()
        self.assertIn("host: lokal.example (lokal) ↔ remote.example (Server)", describe(conflicts[0]))
        self.assertNotIn("x", describe(conflicts[1]).replace("Passwort ist verschieden", ""))  # Passwörter werden nicht angezeigt
        dlg = SyncConflictDialog(conflicts)
        self.assertEqual(dlg.choices(), {"tok1": "local", "tok2": "local"})
        dlg._remote["tok2"].setChecked(True)
        self.assertEqual(dlg.choices(), {"tok1": "local", "tok2": "remote"})
        dlg._set_all("remote")
        self.assertEqual(set(dlg.choices().values()), {"remote"})
        dlg._set_all("local")
        self.assertEqual(set(dlg.choices().values()), {"local"})

    def test_controller_asks_and_applies_choice_without_creating_copies(self):
        from PySide6.QtCore import QEventLoop, QTimer
        from sesam.gui.sync_controller import SyncController
        from sesam.models import Config, Connection, SyncSettings
        from sesam.syncing import SyncEngine

        class Store:
            d = {}
            get = lambda self, k: self.d.get(k)
            set = lambda self, k, v: self.d.__setitem__(k, v)
            delete = lambda self, k: self.d.pop(k, None)

        remote = Path(self.tmp.name) / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
        asked = []

        def controller(name, answer=None):
            cfg = Config(sync=SyncSettings(enabled=True, url=str(remote), passwords=False))
            work = Path(self.tmp.name) / name

            def resolver(conflicts):
                asked.append([c.name for c in conflicts])
                return None if answer is None else {c.token: answer for c in conflicts}

            return cfg, SyncController(cfg, lambda: None, engine_factory=lambda: SyncEngine(cfg.sync, work, None, Store(), device=name),
                                       resolver=resolver)

        def run(c):
            loop = QEventLoop()
            out = {}
            c.finished.connect(lambda r, e, m: (out.update(report=r, error=e), loop.quit()))
            QTimer.singleShot(60000, loop.quit)
            c.sync(manual=True)
            loop.exec()
            return out

        cfgA, A = controller("A", "local")
        conn = Connection("server", "start.example")
        cfgA.connections.append(conn)
        self.assertIsNone(run(A)["error"])
        cfgB, B = controller("B", "remote")
        self.assertIsNone(run(B)["error"])
        conn.host = "von-a"
        self.assertIsNone(run(A)["error"])
        cfgB.connections[0].host = "von-b"

        out = run(B)  # Konflikt: B entscheidet sich für die Server-Version
        self.assertIsNone(out["error"])
        self.assertEqual(asked, [["server"]])
        self.assertEqual([(c.name, c.host) for c in cfgB.connections], [("server", "von-a")])

        cfgB.connections[0].host = "wieder-b"
        conn.host = "wieder-a"
        self.assertIsNone(run(A)["error"])
        B.resolver = lambda cs: {c.token: "local" for c in cs}
        out = run(B)  # diesmal lokal: wird hochgeladen
        self.assertTrue(out["report"].pushed)
        out = run(A)
        self.assertEqual([(c.name, c.host) for c in cfgA.connections], [("server", "wieder-b")])

        # Abbrechen im Dialog: Fehler, keine Änderung
        cfgB.connections[0].host = "b3"
        conn.host = "a3"
        self.assertIsNone(run(A)["error"])
        B.resolver = lambda cs: None
        out = run(B)
        self.assertIsNotNone(out["error"])
        self.assertEqual(cfgB.connections[0].host, "b3")


if __name__ == "__main__":
    unittest.main()
